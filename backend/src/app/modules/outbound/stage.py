"""Tầng Autopilot chạy sau `AgentRunner` trong agent worker (thiết kế 06 A3, §5.1).

`AgentRunner` lưu nháp xong rồi `AutopilotStage` mới đọc nháp đó và gọi cổng gửi `outbound`:
`agent_runtime` vẫn hoàn toàn không import `outbound`. Bị chặn ở cổng ⇒ nháp ở nguyên trong
`agent_drafts` nên nhân viên thấy như chế độ Learning, và lý do chặn được ghi vào `agent_run_steps`.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.agent_management.infrastructure.templates import get_template
from app.modules.agent_runtime.infrastructure.run_requests import RunRequest
from app.modules.outbound.ai_reply import send_ai_reply
from app.modules.outbound.domain.gate import AiGateInput, send_decision
from app.modules.outbound.infrastructure.autopilot import tenant_switches
from app.modules.outbound.infrastructure.monitoring import (
    check_bot_reply_amounts,
    demote_on_config_change,
    record_customer_reaction_if_applicable,
)
from app.shared.infrastructure.metrics import AUTOPILOT_BLOCKED, AUTOPILOT_SENT

log = logging.getLogger(__name__)

Handler = Callable[[RunRequest], Awaitable[None]]

_LATEST_DRAFT = text(
    """
    SELECT r.id AS run_id, r.agent_id, r.intents, r.prompt_version,
           d.id AS draft_id, d.text, d.grounded, d.needs_human, d.based_on_sequence,
           a.template_key, a.template_version, a.mode
    FROM agent_runs r
    JOIN agent_drafts d ON d.tenant_id = r.tenant_id AND d.run_id = r.id
    JOIN agents a ON a.tenant_id = r.tenant_id AND a.id = r.agent_id
    WHERE r.tenant_id = :t AND r.conversation_id = :c AND r.to_sequence = :to
      AND r.status IN ('completed', 'handoff_suggested')
    ORDER BY r.started_at DESC
    LIMIT 1
    """
)


async def _append_gate_step(
    s: AsyncSession, tenant_id: UUID, run_id: UUID, *, status: str, detail: dict
) -> None:
    next_idx = (
        await s.execute(
            text(
                "SELECT COALESCE(MAX(step_index), -1) + 1 FROM agent_run_steps "
                "WHERE tenant_id = :t AND run_id = :r"
            ),
            {"t": tenant_id, "r": run_id},
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO agent_run_steps "
            "(tenant_id, run_id, step_index, kind, name, status, latency_ms, detail) "
            "VALUES (:t, :r, :idx, 'rule', 'send_gate', :st, 0, CAST(:det AS jsonb))"
        ),
        {
            "t": tenant_id,
            "r": run_id,
            "idx": next_idx,
            "st": status,
            "det": json.dumps(detail, ensure_ascii=False),
        },
    )


async def _validation_problems(s: AsyncSession, tenant_id: UUID, run_id: UUID) -> tuple[str, ...]:
    row = (
        await s.execute(
            text(
                "SELECT detail FROM agent_run_steps "
                "WHERE tenant_id = :t AND run_id = :r AND kind = 'validate' AND name = 'draft' "
                "ORDER BY step_index DESC LIMIT 1"
            ),
            {"t": tenant_id, "r": run_id},
        )
    ).first()
    if row is None or not row.detail:
        return ()
    return tuple(str(p) for p in (row.detail.get("problems") or ()))


class AutopilotStage:
    """Chạy ngay sau `AgentRunner`, cùng `RunRequest`, không gọi LLM."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        runner_handler: Handler,
    ) -> None:
        self._factory = session_factory
        self._runner = runner_handler

    async def handle(self, req: RunRequest) -> None:
        await self._runner(req)
        await self._record_reaction(req)
        await self.try_send(req)

    async def _record_reaction(self, req: RunRequest) -> None:
        """Ghi tín hiệu phản ứng xấu của khách nếu lượt mới ngay sau một tin bot (06 §6.1)."""
        async with self._factory() as s, s.begin():
            run = (
                await s.execute(
                    text(
                        "SELECT intents FROM agent_runs "
                        "WHERE tenant_id = :t AND conversation_id = :c AND to_sequence = :to "
                        "ORDER BY started_at DESC LIMIT 1"
                    ),
                    {"t": req.tenant_id, "c": req.conversation_id, "to": req.to_sequence},
                )
            ).first()
            if run is None:
                return
            cust_rows = (
                await s.execute(
                    text(
                        "SELECT content FROM messages "
                        "WHERE tenant_id = :t AND conversation_id = :c "
                        "  AND sender_type = 'customer' "
                        "  AND sequence BETWEEN :frm AND :to AND content IS NOT NULL "
                        "ORDER BY sequence"
                    ),
                    {
                        "t": req.tenant_id,
                        "c": req.conversation_id,
                        "frm": req.from_sequence,
                        "to": req.to_sequence,
                    },
                )
            ).all()
            await record_customer_reaction_if_applicable(
                s,
                req.tenant_id,
                req.conversation_id,
                next_intents=tuple(run.intents or ()),
                next_customer_texts=[r[0] for r in cust_rows],
                run_from_sequence=req.from_sequence,
            )

    async def try_send(self, req: RunRequest) -> str | None:
        """Trả `'sent'`, `'duplicate'`, lý do chặn, hoặc `None` nếu không có nháp Learning."""
        async with self._factory() as s:
            draft = (
                await s.execute(
                    _LATEST_DRAFT,
                    {"t": req.tenant_id, "c": req.conversation_id, "to": req.to_sequence},
                )
            ).first()
            if draft is None or draft.mode != "learning":
                return None
            problems = await _validation_problems(s, req.tenant_id, draft.run_id)

        tpl = get_template(draft.template_key, draft.template_version)
        handoff_intents = frozenset(tpl.handoff_intents) if tpl else frozenset()
        intents = tuple(draft.intents or ())
        run_config_key = {
            "template": draft.template_key,
            "template_version": draft.template_version,
            "prompt_version": draft.prompt_version,
        }

        async with self._factory() as s, s.begin():
            _, _, max_bot_streak = await tenant_switches(s, req.tenant_id)

            async def _recheck_under_lock(tx: AsyncSession) -> str | None:
                ai_enabled, auto_send, streak_limit = await tenant_switches(tx, req.tenant_id)
                rows = (
                    await tx.execute(
                        text(
                            "SELECT intent, config_key FROM autopilot_intents "
                            "WHERE tenant_id = :t AND agent_id = :a AND state = 'autopilot'"
                        ),
                        {"t": req.tenant_id, "a": draft.agent_id},
                    )
                ).all()
                enabled = frozenset(r.intent for r in rows)
                cfg_keys = {r.intent: r.config_key for r in rows}
                return send_decision(
                    AiGateInput(
                        ai_enabled=ai_enabled,
                        auto_send=auto_send,
                        intents=intents,
                        enabled_intents=enabled,
                        handoff_intents=handoff_intents,
                        run_config_key=run_config_key,
                        enabled_config_keys=cfg_keys,
                        grounded=bool(draft.grounded),
                        validation_problems=problems,
                        needs_human=bool(draft.needs_human),
                        # handling/status/stale/bot_streak/channel được `send_ai_reply` kiểm lại
                        # trực tiếp dưới khóa `FOR UPDATE OF c` ngay sau callback này.
                        handling="bot",
                        conversation_status="open",
                        based_on_sequence=draft.based_on_sequence,
                        latest_customer_sequence=draft.based_on_sequence,
                        human_replied_after=False,
                        bot_streak=0,
                        max_bot_streak=streak_limit,
                        provider="webchat",
                        channel_allowed=True,
                        catalog_stale=False,
                    )
                )

            res = await send_ai_reply(
                s,
                tenant_id=req.tenant_id,
                conversation_id=req.conversation_id,
                draft_id=draft.draft_id,
                run_id=draft.run_id,
                based_on_sequence=draft.based_on_sequence,
                content=draft.text,
                max_bot_streak=max_bot_streak,
                recheck=_recheck_under_lock,
            )
            if res.duplicate:
                return "duplicate"
            if res.sent:
                await _append_gate_step(
                    s,
                    req.tenant_id,
                    draft.run_id,
                    status="sent",
                    detail={"message_id": str(res.message_id), "intents": list(intents)},
                )
                primary = intents[0] if intents else "unknown"
                AUTOPILOT_SENT.labels(intent=primary).inc()
                if res.message_id is not None:
                    await check_bot_reply_amounts(
                        s,
                        req.tenant_id,
                        message_id=res.message_id,
                        agent_id=draft.agent_id,
                        intent=primary,
                        content=draft.text,
                    )
                log.info(
                    "autopilot sent reply",
                    extra={
                        "conversation_id": str(req.conversation_id),
                        "draft_id": str(draft.draft_id),
                        "intent": primary,
                    },
                )
                return "sent"

            reason = res.blocked_reason or "unknown"
            if reason == "config_changed":
                await demote_on_config_change(
                    s, req.tenant_id, draft.agent_id, current_config_key=run_config_key
                )
            await _append_gate_step(
                s,
                req.tenant_id,
                draft.run_id,
                status="blocked",
                detail={"reason": reason},
            )
            AUTOPILOT_BLOCKED.labels(reason=reason).inc()
            return reason
