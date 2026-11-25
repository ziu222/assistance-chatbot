"""Theo dõi sau khi gửi tin bot (`ai_reply_followups`) và tự hạ cấp (thiết kế 06 §6, A4)."""

import json
from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge.domain.releases import render_knowledge
from app.modules.knowledge.infrastructure.releases import active_snapshot
from app.modules.outbound.domain.monitoring import (
    BAD_REACTION_INTENTS,
    CORRECTION_WINDOW_SECONDS,
    DAILY_SAMPLE_LIMIT_PER_INTENT,
    DEMOTE_WINDOW_REPLIES,
    OwnerReplyLabel,
    classify_staff_correction,
    demotion_reason,
    is_repeat_question,
)
from app.shared.domain.text import extract_amounts
from app.shared.infrastructure.metrics import AUTOPILOT_DEMOTED
from app.shared.infrastructure.signals import record_signal


async def demote_intent(
    s: AsyncSession, tenant_id: UUID, agent_id: UUID, intent: str, *, reason: str
) -> bool:
    """Hạ cấp một ý định đang `autopilot` về lại `learning`, ghi signal và metric."""
    row = (
        await s.execute(
            text(
                "UPDATE autopilot_intents "
                "SET state = 'learning', demoted_at = now(), demote_reason = :r, "
                "    updated_at = now() "
                "WHERE tenant_id = :t AND agent_id = :a AND intent = :i AND state = 'autopilot' "
                "RETURNING intent"
            ),
            {"t": tenant_id, "a": agent_id, "i": intent, "r": reason},
        )
    ).first()
    if row is None:
        return False
    await record_signal(
        s,
        tenant_id,
        None,
        "autopilot_demoted",
        {"agent_id": str(agent_id), "intent": intent, "reason": reason},
    )
    AUTOPILOT_DEMOTED.labels(reason=reason).inc()
    return True


async def demote_on_config_change(
    s: AsyncSession, tenant_id: UUID, agent_id: UUID, *, current_config_key: dict
) -> list[str]:
    """Đổi template / phiên bản template / PROMPT_VERSION ⇒ hạ cấp mọi ý định lệch cấu hình."""
    rows = (
        await s.execute(
            text(
                "SELECT intent FROM autopilot_intents "
                "WHERE tenant_id = :t AND agent_id = :a AND state = 'autopilot' "
                "  AND config_key <> CAST(:cfg AS jsonb)"
            ),
            {"t": tenant_id, "a": agent_id, "cfg": json.dumps(current_config_key)},
        )
    ).all()
    demoted: list[str] = []
    for (intent,) in rows:
        if await demote_intent(s, tenant_id, agent_id, intent, reason="config_changed"):
            demoted.append(intent)
    return demoted


async def evaluate_and_demote(
    s: AsyncSession, tenant_id: UUID, agent_id: UUID, intent: str
) -> str | None:
    """Đánh giá cửa sổ 50 tin bot gần nhất của ý định và tự hạ cấp ngay nếu vượt ngưỡng §6.2."""
    row = (
        await s.execute(
            text(
                "SELECT enabled_at FROM autopilot_intents "
                "WHERE tenant_id = :t AND agent_id = :a AND intent = :i AND state = 'autopilot'"
            ),
            {"t": tenant_id, "a": agent_id, "i": intent},
        )
    ).first()
    if row is None:
        return None
    enabled_at = row.enabled_at

    recent_msgs = (
        await s.execute(
            text(
                "SELECT m.id FROM messages m "
                "JOIN agent_drafts d ON d.tenant_id = m.tenant_id AND d.id = m.draft_id "
                "JOIN agent_runs r ON r.tenant_id = d.tenant_id AND r.id = d.run_id "
                "WHERE m.tenant_id = :t AND m.sender_type = 'bot' AND r.agent_id = :a "
                "  AND :i = ANY(r.intents) "
                "  AND (CAST(:since AS timestamptz) IS NULL "
                "       OR m.occurred_at >= CAST(:since AS timestamptz)) "
                "ORDER BY m.occurred_at DESC LIMIT :lim"
            ),
            {
                "t": tenant_id,
                "a": agent_id,
                "i": intent,
                "since": enabled_at,
                "lim": DEMOTE_WINDOW_REPLIES,
            },
        )
    ).all()
    msg_ids = [r[0] for r in recent_msgs]
    if not msg_ids:
        return None

    counts = (
        await s.execute(
            text(
                "SELECT "
                "  count(*) FILTER (WHERE kind = 'amount_conflict') AS amount_conflicts, "
                "  count(*) FILTER ("
                "    WHERE kind = 'owner_label' AND detail->>'label' = 'wrong' "
                "      AND coalesce((detail->>'wrong_fact')::boolean, false)"
                "  ) AS owner_wrong_fact, "
                "  count(*) FILTER ("
                "    WHERE kind = 'staff_correction' "
                "      AND coalesce((detail->>'amount_changed')::boolean, false)"
                "  ) AS staff_amount_corrections, "
                "  count(*) FILTER (WHERE kind = 'customer_reaction') AS customer_reactions "
                "FROM ai_reply_followups "
                "WHERE tenant_id = :t AND message_id = ANY(:mids)"
            ),
            {"t": tenant_id, "mids": msg_ids},
        )
    ).one()

    reason = demotion_reason(
        len(msg_ids),
        amount_conflicts=counts.amount_conflicts,
        owner_wrong_fact=counts.owner_wrong_fact,
        staff_amount_corrections=counts.staff_amount_corrections,
        customer_reactions=counts.customer_reactions,
    )
    if reason is not None:
        await demote_intent(s, tenant_id, agent_id, intent, reason=reason)
    return reason


async def record_staff_correction_if_applicable(
    s: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    *,
    staff_sequence: int,
    staff_text: str,
) -> str | None:
    """Ghi `staff_correction` nếu nhân viên đính chính trong 10 phút sau tin bot."""
    bot_msg = (
        await s.execute(
            text(
                "SELECT m.id, m.sequence, m.content, r.agent_id, r.intents "
                "FROM messages m "
                "JOIN agent_drafts d ON d.tenant_id = m.tenant_id AND d.id = m.draft_id "
                "JOIN agent_runs r ON r.tenant_id = d.tenant_id AND r.id = d.run_id "
                "WHERE m.tenant_id = :t AND m.conversation_id = :c "
                "  AND m.sender_type = 'bot' AND m.sequence < :seq "
                "  AND m.occurred_at >= now() - make_interval(secs => :win) "
                "  AND NOT EXISTS ("
                "    SELECT 1 FROM messages c2 WHERE c2.tenant_id = m.tenant_id "
                "      AND c2.conversation_id = m.conversation_id "
                "      AND c2.sender_type = 'customer' "
                "      AND c2.sequence > m.sequence AND c2.sequence < :seq"
                "  ) "
                "ORDER BY m.sequence DESC LIMIT 1"
            ),
            {
                "t": tenant_id,
                "c": conversation_id,
                "seq": staff_sequence,
                "win": CORRECTION_WINDOW_SECONDS,
            },
        )
    ).first()
    if bot_msg is None:
        return None

    intent = (bot_msg.intents or ["other"])[0]
    detail = classify_staff_correction(bot_msg.content or "", staff_text)
    await s.execute(
        text(
            "INSERT INTO ai_reply_followups "
            "(id, tenant_id, message_id, agent_id, intent, kind, detail) "
            "VALUES (:id, :t, :mid, :aid, :i, 'staff_correction', CAST(:det AS jsonb)) "
            "ON CONFLICT (tenant_id, message_id, kind) DO NOTHING"
        ),
        {
            "id": uuid4(),
            "t": tenant_id,
            "mid": bot_msg.id,
            "aid": bot_msg.agent_id,
            "i": intent,
            "det": json.dumps(detail, ensure_ascii=False),
        },
    )
    return await evaluate_and_demote(s, tenant_id, bot_msg.agent_id, intent)


async def record_customer_reaction_if_applicable(
    s: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    *,
    next_intents: Sequence[str],
    next_customer_texts: Sequence[str],
    run_from_sequence: int,
) -> str | None:
    """Ghi `customer_reaction` nếu lượt khách sau tin bot là khiếu nại/đòi người hoặc hỏi lại."""
    bot_msg = (
        await s.execute(
            text(
                "SELECT m.id, m.sequence, d.based_on_sequence, r.agent_id, r.intents "
                "FROM messages m "
                "JOIN agent_drafts d ON d.tenant_id = m.tenant_id AND d.id = m.draft_id "
                "JOIN agent_runs r ON r.tenant_id = d.tenant_id AND r.id = d.run_id "
                "WHERE m.tenant_id = :t AND m.conversation_id = :c "
                "  AND m.sender_type = 'bot' AND m.sequence < :frm "
                "  AND NOT EXISTS ("
                "    SELECT 1 FROM messages h WHERE h.tenant_id = m.tenant_id "
                "      AND h.conversation_id = m.conversation_id "
                "      AND h.sender_type = 'human' "
                "      AND h.sequence > m.sequence AND h.sequence < :frm"
                "  ) "
                "ORDER BY m.sequence DESC LIMIT 1"
            ),
            {"t": tenant_id, "c": conversation_id, "frm": run_from_sequence},
        )
    ).first()
    if bot_msg is None:
        return None

    bad_intent = bool(set(next_intents) & BAD_REACTION_INTENTS)
    repeated = False
    if not bad_intent and next_customer_texts:
        prev_cust = (
            await s.execute(
                text(
                    "SELECT content FROM messages "
                    "WHERE tenant_id = :t AND conversation_id = :c "
                    "  AND sender_type = 'customer' AND sequence <= :seq "
                    "ORDER BY sequence DESC LIMIT 1"
                ),
                {"t": tenant_id, "c": conversation_id, "seq": bot_msg.based_on_sequence},
            )
        ).scalar_one_or_none()
        if prev_cust:
            repeated = any(is_repeat_question(prev_cust, nxt) for nxt in next_customer_texts)

    if not bad_intent and not repeated:
        return None

    intent = (bot_msg.intents or ["other"])[0]
    detail = {"intents": list(next_intents), "repeated": repeated}
    await s.execute(
        text(
            "INSERT INTO ai_reply_followups "
            "(id, tenant_id, message_id, agent_id, intent, kind, detail) "
            "VALUES (:id, :t, :mid, :aid, :i, 'customer_reaction', CAST(:det AS jsonb)) "
            "ON CONFLICT (tenant_id, message_id, kind) DO NOTHING"
        ),
        {
            "id": uuid4(),
            "t": tenant_id,
            "mid": bot_msg.id,
            "aid": bot_msg.agent_id,
            "i": intent,
            "det": json.dumps(detail, ensure_ascii=False),
        },
    )
    return await evaluate_and_demote(s, tenant_id, bot_msg.agent_id, intent)


async def check_bot_reply_amounts(
    s: AsyncSession,
    tenant_id: UUID,
    *,
    message_id: UUID,
    agent_id: UUID,
    intent: str,
    content: str,
) -> bool:
    """Kiểm số tiền sau gửi đối chiếu catalog và tri thức hiện hành; lệch ⇒ hạ cấp ngay."""
    found = extract_amounts(content)
    if not found:
        return False
    rendered = render_knowledge(await active_snapshot(s, tenant_id))
    known = extract_amounts(rendered.text)
    prices = await s.execute(
        text("SELECT DISTINCT price_vnd FROM catalog_items WHERE tenant_id = :t"),
        {"t": tenant_id},
    )
    known |= {int(r[0]) for r in prices if r[0] is not None}
    unknown = sorted(found - known)
    if not unknown:
        return False

    await s.execute(
        text(
            "INSERT INTO ai_reply_followups "
            "(id, tenant_id, message_id, agent_id, intent, kind, detail) "
            "VALUES (:id, :t, :mid, :aid, :i, 'amount_conflict', CAST(:det AS jsonb)) "
            "ON CONFLICT (tenant_id, message_id, kind) DO NOTHING"
        ),
        {
            "id": uuid4(),
            "t": tenant_id,
            "mid": message_id,
            "aid": agent_id,
            "i": intent,
            "det": json.dumps({"unknown_amounts": unknown}),
        },
    )
    await evaluate_and_demote(s, tenant_id, agent_id, intent)
    return True


async def list_replies_for_review(
    s: AsyncSession,
    tenant_id: UUID,
    *,
    intent: str | None = None,
    days: int = 7,
    per_intent_per_day: int = DAILY_SAMPLE_LIMIT_PER_INTENT,
) -> list[dict]:
    """Lấy mẫu tối đa 10 tin bot mỗi ngày mỗi ý định kèm tín hiệu theo dõi để chủ shop chấm."""
    cond = "AND r.intents[1] = :intent" if intent else ""
    params: dict = {"t": tenant_id, "d": days, "lim": per_intent_per_day}
    if intent:
        params["intent"] = intent
    rows = (
        await s.execute(
            text(
                "WITH ranked AS ("
                "  SELECT m.id, m.conversation_id, m.content AS bot_text, m.occurred_at, "
                "         d.based_on_sequence, r.agent_id, "
                "         coalesce(r.intents[1], 'other') AS intent, "
                "         row_number() OVER ("
                "           PARTITION BY coalesce(r.intents[1], 'other'), "
                "                        date_trunc('day', m.occurred_at AT TIME ZONE 'UTC') "
                "           ORDER BY m.occurred_at DESC"
                "         ) AS rn "
                "  FROM messages m "
                "  JOIN agent_drafts d ON d.tenant_id = m.tenant_id AND d.id = m.draft_id "
                "  JOIN agent_runs r ON r.tenant_id = d.tenant_id AND r.id = d.run_id "
                f" WHERE m.tenant_id = :t AND m.sender_type = 'bot' {cond} "
                "    AND m.occurred_at > now() - make_interval(days => :d)"
                ") "
                "SELECT rk.id, rk.conversation_id, rk.bot_text, rk.occurred_at, rk.agent_id, "
                "       rk.intent, ("
                "         SELECT c.content FROM messages c "
                "         WHERE c.tenant_id = :t AND c.conversation_id = rk.conversation_id "
                "           AND c.sender_type = 'customer' AND c.sequence <= rk.based_on_sequence "
                "         ORDER BY c.sequence DESC LIMIT 1"
                "       ) AS customer_text "
                "FROM ranked rk WHERE rk.rn <= :lim "
                "ORDER BY rk.occurred_at DESC LIMIT 100"
            ),
            params,
        )
    ).all()
    if not rows:
        return []

    msg_ids = [r.id for r in rows]
    frows = (
        await s.execute(
            text(
                "SELECT message_id, kind, detail, created_at FROM ai_reply_followups "
                "WHERE tenant_id = :t AND message_id = ANY(:mids) ORDER BY created_at"
            ),
            {"t": tenant_id, "mids": msg_ids},
        )
    ).all()
    by_msg: dict[UUID, list[dict]] = {}
    for f in frows:
        by_msg.setdefault(f.message_id, []).append(
            {"kind": f.kind, "detail": f.detail, "created_at": f.created_at.isoformat()}
        )

    items: list[dict] = []
    for r in rows:
        fups = by_msg.get(r.id, [])
        owner_lbl = next((x["detail"] for x in fups if x["kind"] == "owner_label"), None)
        items.append(
            {
                "message_id": str(r.id),
                "conversation_id": str(r.conversation_id),
                "agent_id": str(r.agent_id),
                "intent": r.intent,
                "customer_text": r.customer_text or "",
                "bot_text": r.bot_text or "",
                "occurred_at": r.occurred_at.isoformat(),
                "followups": fups,
                "owner_label": owner_lbl,
            }
        )
    return items


async def label_bot_reply(
    s: AsyncSession,
    tenant_id: UUID,
    message_id: UUID,
    *,
    label: OwnerReplyLabel,
    wrong_fact: bool,
    user_id: UUID,
) -> dict | None:
    """Chủ shop chấm mẫu một tin bot; nếu sai số tiền/sản phẩm ⇒ tự hạ cấp ngay."""
    row = (
        await s.execute(
            text(
                "SELECT m.id, r.agent_id, coalesce(r.intents[1], 'other') AS intent "
                "FROM messages m "
                "JOIN agent_drafts d ON d.tenant_id = m.tenant_id AND d.id = m.draft_id "
                "JOIN agent_runs r ON r.tenant_id = d.tenant_id AND r.id = d.run_id "
                "WHERE m.tenant_id = :t AND m.id = :mid AND m.sender_type = 'bot'"
            ),
            {"t": tenant_id, "mid": message_id},
        )
    ).first()
    if row is None:
        return None

    detail = {"label": label, "wrong_fact": wrong_fact, "labeled_by": str(user_id)}
    await s.execute(
        text(
            "INSERT INTO ai_reply_followups "
            "(id, tenant_id, message_id, agent_id, intent, kind, detail) "
            "VALUES (:id, :t, :mid, :aid, :i, 'owner_label', CAST(:det AS jsonb)) "
            "ON CONFLICT (tenant_id, message_id, kind) DO UPDATE "
            "SET detail = EXCLUDED.detail, created_at = now()"
        ),
        {
            "id": uuid4(),
            "t": tenant_id,
            "mid": message_id,
            "aid": row.agent_id,
            "i": row.intent,
            "det": json.dumps(detail, ensure_ascii=False),
        },
    )
    demoted = await evaluate_and_demote(s, tenant_id, row.agent_id, row.intent)
    return {
        "message_id": str(message_id),
        "intent": row.intent,
        "label": label,
        "wrong_fact": wrong_fact,
        "demoted_reason": demoted,
    }
