"""Quản lý trạng thái Autopilot theo từng ý định (`autopilot_intents`) và nút dừng (thiết kế 06 A2).

Mọi thay đổi trạng thái đều kiểm lại mức sẵn sàng phía server (không tin client) và ghi tín hiệu
`autopilot_enabled` / `autopilot_demoted` (chỉ id và nhãn).
"""

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_management.infrastructure.templates import get_template
from app.modules.agent_runtime.llm.routes import load_routes
from app.modules.agent_runtime.prompts import PROMPT_VERSION
from app.modules.outbound.domain.autopilot import (
    ENABLEABLE_INTENTS,
    ReadinessSnapshot,
    Source,
    can_enable_intent,
    extract_config_key,
    primary_models,
)
from app.modules.outbound.domain.channels import DEFAULT_CATALOG_MAX_AGE_HOURS
from app.modules.outbound.domain.gate import DEFAULT_MAX_BOT_STREAK
from app.modules.shadow.infrastructure.summary import summary as shadow_summary
from app.shared.infrastructure.signals import record_signal


class AgentNotFound(Exception):
    pass


class AutopilotNotEligible(Exception):
    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = list(reasons)
        super().__init__("; ".join(reasons))


async def _resolve_agent(s: AsyncSession, tenant_id: UUID, agent_id: UUID | None):
    if agent_id is not None:
        return (
            await s.execute(
                text(
                    "SELECT id, template_key, template_version, mode FROM agents "
                    "WHERE tenant_id = :t AND id = :a"
                ),
                {"t": tenant_id, "a": agent_id},
            )
        ).first()
    return (
        await s.execute(
            text(
                "SELECT id, template_key, template_version, mode FROM agents "
                "WHERE tenant_id = :t ORDER BY created_at ASC LIMIT 1"
            ),
            {"t": tenant_id},
        )
    ).first()


async def tenant_switches(s: AsyncSession, tenant_id: UUID) -> tuple[bool, bool, int]:
    """Trả `(ai_enabled, auto_send, max_bot_streak)` của tenant."""
    ai = (
        await s.execute(
            text("SELECT ai_enabled FROM tenant_ai_settings WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    pol = (
        await s.execute(
            text("SELECT auto_send, max_bot_streak FROM tenant_policy WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
    ).first()
    return (
        bool(ai) if ai is not None else False,
        bool(pol.auto_send) if pol is not None else False,
        int(pol.max_bot_streak) if pol is not None else DEFAULT_MAX_BOT_STREAK,
    )


async def catalog_max_age_hours(s: AsyncSession, tenant_id: UUID) -> int:
    """Ngưỡng độ mới catalog (giờ) của tenant để kiểm `catalog_stale` cho `price_quote`."""
    val = (
        await s.execute(
            text("SELECT catalog_max_age_hours FROM tenant_policy WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    return int(val) if val is not None else DEFAULT_CATALOG_MAX_AGE_HOURS


def _snapshot_map(rows: list[dict]) -> dict[str, ReadinessSnapshot]:
    out: dict[str, ReadinessSnapshot] = {}
    for item in rows:
        intent = item.get("intent")
        if not intent:
            continue
        raw_cfg = item.get("config")
        parsed_cfg = json.loads(raw_cfg) if isinstance(raw_cfg, str) else raw_cfg
        out[intent] = ReadinessSnapshot(
            ready=bool(item.get("ready")),
            reasons=tuple(item.get("not_ready_because") or ()),
            config_key=extract_config_key(parsed_cfg),
        )
    return out


async def _load_rows(s: AsyncSession, tenant_id: UUID, agent_id: UUID) -> dict[str, dict]:
    rows = (
        await s.execute(
            text(
                "SELECT intent, state, config_key, source, enabled_by, enabled_at, "
                "       demoted_at, demote_reason "
                "FROM autopilot_intents WHERE tenant_id = :t AND agent_id = :a"
            ),
            {"t": tenant_id, "a": agent_id},
        )
    ).all()
    return {
        r.intent: {
            "intent": r.intent,
            "state": r.state,
            "config_key": r.config_key,
            "source": r.source,
            "enabled_by": str(r.enabled_by) if r.enabled_by else None,
            "enabled_at": r.enabled_at.isoformat() if r.enabled_at else None,
            "demoted_at": r.demoted_at.isoformat() if r.demoted_at else None,
            "demote_reason": r.demote_reason,
        }
        for r in rows
    }


async def list_autopilot_intents(
    s: AsyncSession, tenant_id: UUID, *, agent_id: UUID | None = None, days: int = 14
) -> dict:
    ai_enabled, auto_send, max_bot_streak = await tenant_switches(s, tenant_id)
    agent = await _resolve_agent(s, tenant_id, agent_id)
    if agent is None:
        return {
            "agent_id": None,
            "ai_enabled": ai_enabled,
            "auto_send": auto_send,
            "max_bot_streak": max_bot_streak,
            "items": [],
        }

    tpl = get_template(agent.template_key, agent.template_version)
    handoff_intents = frozenset(tpl.handoff_intents) if tpl else frozenset()
    models = primary_models(load_routes(), tpl)
    current_cfg = (
        {
            "template": agent.template_key,
            "template_version": agent.template_version,
            "prompt_version": PROMPT_VERSION,
            "models": models,
        }
        if models is not None
        else None
    )
    sum_data = await shadow_summary(
        s, tenant_id, days, model=models["draft"] if models is not None else None
    )
    blind_map = _snapshot_map(sum_data["intents"])
    shown_map = _snapshot_map(sum_data.get("learning_intents") or [])
    existing = await _load_rows(s, tenant_id, agent.id)

    all_intents = sorted(ENABLEABLE_INTENTS | set(existing) | set(blind_map) | set(shown_map))
    items: list[dict] = []
    for intent in all_intents:
        row = existing.get(intent)
        decision = can_enable_intent(
            intent,
            handoff_intents=handoff_intents,
            current_config_key=current_cfg,
            blind=blind_map.get(intent),
            shown=shown_map.get(intent),
            previously_enabled=row is not None and row["enabled_at"] is not None,
        )
        items.append(
            {
                "intent": intent,
                "state": row["state"] if row else "learning",
                "config_key": row["config_key"] if row else None,
                "source": row["source"] if row else None,
                "enabled_at": row["enabled_at"] if row else None,
                "demoted_at": row["demoted_at"] if row else None,
                "demote_reason": row["demote_reason"] if row else None,
                "can_enable": decision.allowed,
                "enable_source": decision.source,
                "enable_reasons": list(decision.reasons),
                "blind_ready": bool(blind_map[intent].ready) if intent in blind_map else False,
                "shown_ready": bool(shown_map[intent].ready) if intent in shown_map else False,
            }
        )
    return {
        "agent_id": str(agent.id),
        "ai_enabled": ai_enabled,
        "auto_send": auto_send,
        "max_bot_streak": max_bot_streak,
        "items": items,
    }


async def enable_intent(
    s: AsyncSession,
    tenant_id: UUID,
    intent: str,
    *,
    user_id: UUID,
    agent_id: UUID | None = None,
    preferred_source: Source | None = None,
    days: int = 14,
) -> dict:
    """Bật Autopilot cho một ý định sau khi kiểm lại mức sẵn sàng phía server."""
    agent = await _resolve_agent(s, tenant_id, agent_id)
    if agent is None:
        raise AgentNotFound("Agent not found")

    tpl = get_template(agent.template_key, agent.template_version)
    handoff_intents = frozenset(tpl.handoff_intents) if tpl else frozenset()
    models = primary_models(load_routes(), tpl)
    current_cfg = (
        {
            "template": agent.template_key,
            "template_version": agent.template_version,
            "prompt_version": PROMPT_VERSION,
            "models": models,
        }
        if models is not None
        else None
    )
    sum_data = await shadow_summary(
        s, tenant_id, days, model=models["draft"] if models is not None else None
    )
    blind_map = _snapshot_map(sum_data["intents"])
    shown_map = _snapshot_map(sum_data.get("learning_intents") or [])
    existing = await _load_rows(s, tenant_id, agent.id)
    prev = existing.get(intent)

    decision = can_enable_intent(
        intent,
        handoff_intents=handoff_intents,
        current_config_key=current_cfg,
        blind=blind_map.get(intent),
        shown=shown_map.get(intent),
        previously_enabled=prev is not None and prev["enabled_at"] is not None,
        preferred_source=preferred_source,
    )
    if not decision.allowed or decision.source is None or decision.config_key is None:
        raise AutopilotNotEligible(decision.reasons)

    row = (
        await s.execute(
            text(
                "INSERT INTO autopilot_intents "
                "(tenant_id, agent_id, intent, state, config_key, source, enabled_by, "
                " enabled_at, demoted_at, demote_reason, updated_at) "
                "VALUES (:t, :a, :i, 'autopilot', CAST(:cfg AS jsonb), :src, :by, "
                " now(), NULL, NULL, now()) "
                "ON CONFLICT (tenant_id, agent_id, intent) DO UPDATE "
                "SET state = 'autopilot', config_key = EXCLUDED.config_key, "
                "    source = EXCLUDED.source, enabled_by = EXCLUDED.enabled_by, "
                "    enabled_at = now(), demoted_at = NULL, demote_reason = NULL, "
                "    updated_at = now() "
                "RETURNING intent, state, config_key, source, enabled_by, enabled_at, "
                "          demoted_at, demote_reason"
            ),
            {
                "t": tenant_id,
                "a": agent.id,
                "i": intent,
                "cfg": json.dumps(decision.config_key),
                "src": decision.source,
                "by": user_id,
            },
        )
    ).one()
    await record_signal(
        s,
        tenant_id,
        None,
        "autopilot_enabled",
        {
            "agent_id": str(agent.id),
            "intent": intent,
            "source": decision.source,
            "enabled_by": str(user_id),
        },
    )
    return {
        "agent_id": str(agent.id),
        "intent": row.intent,
        "state": row.state,
        "config_key": row.config_key,
        "source": row.source,
        "enabled_by": str(row.enabled_by) if row.enabled_by else None,
        "enabled_at": row.enabled_at.isoformat() if row.enabled_at else None,
        "demoted_at": None,
        "demote_reason": None,
    }


async def disable_intent(
    s: AsyncSession,
    tenant_id: UUID,
    intent: str,
    *,
    user_id: UUID,
    agent_id: UUID | None = None,
    reason: str = "manual_disable",
) -> dict | None:
    """Tắt Autopilot của một ý định về lại `learning`."""
    agent = await _resolve_agent(s, tenant_id, agent_id)
    if agent is None:
        return None
    row = (
        await s.execute(
            text(
                "UPDATE autopilot_intents "
                "SET state = 'learning', demoted_at = now(), demote_reason = :r, "
                "    updated_at = now() "
                "WHERE tenant_id = :t AND agent_id = :a AND intent = :i "
                "RETURNING intent, state, config_key, source, enabled_by, enabled_at, "
                "          demoted_at, demote_reason"
            ),
            {"t": tenant_id, "a": agent.id, "i": intent, "r": reason},
        )
    ).first()
    if row is None:
        return None
    await record_signal(
        s,
        tenant_id,
        None,
        "autopilot_demoted",
        {
            "agent_id": str(agent.id),
            "intent": intent,
            "reason": reason,
            "by": str(user_id),
        },
    )
    return {
        "agent_id": str(agent.id),
        "intent": row.intent,
        "state": row.state,
        "config_key": row.config_key,
        "source": row.source,
        "enabled_by": str(row.enabled_by) if row.enabled_by else None,
        "enabled_at": row.enabled_at.isoformat() if row.enabled_at else None,
        "demoted_at": row.demoted_at.isoformat() if row.demoted_at else None,
        "demote_reason": row.demote_reason,
    }


async def pause_autopilot(s: AsyncSession, tenant_id: UUID, *, user_id: UUID) -> dict:
    """Nút dừng khẩn cấp: tắt ngay `tenant_policy.auto_send` của tenant."""
    result = await s.execute(
        text("UPDATE tenant_policy SET auto_send = false, updated_at = now() WHERE tenant_id = :t"),
        {"t": tenant_id},
    )
    await record_signal(
        s,
        tenant_id,
        None,
        "autopilot_demoted",
        {"reason": "manual_pause", "by": str(user_id)},
    )
    return {"auto_send": False, "updated": result.rowcount == 1}
