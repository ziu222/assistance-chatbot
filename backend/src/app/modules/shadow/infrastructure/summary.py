"""Tổng hợp so sánh shadow theo ý định và mức sẵn sàng (thiết kế 04 §7, giai đoạn S4).

Mỗi ý định chỉ tính các cặp thuộc phiên bản cấu hình AI HIỆN TẠI của nó (phiên bản của cặp mới
nhất): template, phiên bản template, `PROMPT_VERSION`, `knowledge_hash`. Model phục vụ không nằm
trong khóa vì failover có thể đổi model giữa các run; nó được liệt kê để người đọc thấy.
Nháp bị Atlas chặn (`not_sendable`) làm giảm tỉ lệ gửi được nhưng không tính là "sai": cổng gửi
không bao giờ gửi chúng.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.shadow.domain.readiness import IntentStats, Readiness, rate, readiness
from app.modules.shadow.infrastructure.labels import calibration

_SUMMARY = text(
    """
    WITH latest AS (
      SELECT DISTINCT ON (intent) intent, config_version - 'model' AS key
      FROM shadow_comparisons WHERE tenant_id = :t
      ORDER BY intent, created_at DESC
    ), c AS (
      SELECT sc.*, l.key FROM shadow_comparisons sc
      JOIN latest l ON sc.intent IS NOT DISTINCT FROM l.intent
       AND (sc.config_version - 'model') = l.key
      WHERE sc.tenant_id = :t AND sc.created_at > now() - make_interval(days => :d)
    )
    SELECT intent, min(key::text) AS config,
      count(*) AS total,
      count(*) FILTER (WHERE status = 'unanswered') AS unanswered,
      count(*) FILTER (WHERE final_verdict IS NOT NULL) AS scored,
      count(*) FILTER (WHERE sendable IN ('as_is', 'minor_edit')) AS sendable,
      count(*) FILTER (WHERE final_verdict IS DISTINCT FROM 'not_sendable'
        AND (final_verdict = 'wrong' OR sendable = 'must_not_send')) AS wrong,
      count(*) FILTER (WHERE code_checks->>'amounts' = 'conflict'
        AND NOT coalesce((code_checks->>'reference_suspect')::boolean, false)
        AND created_at > now() - interval '7 days') AS conflicts,
      count(*) FILTER (WHERE code_checks->>'handoff' = 'extra') AS handoff_extra,
      count(*) FILTER (WHERE code_checks->>'handoff' = 'missing') AS handoff_missing,
      percentile_cont(0.5) WITHIN GROUP (ORDER BY bot_lead_seconds)
        FILTER (WHERE bot_lead_seconds IS NOT NULL) AS median_lead,
      array_remove(array_agg(DISTINCT config_version->>'model'), NULL) AS models
    FROM c GROUP BY intent ORDER BY count(*) DESC
    """
)


def _json(s: IntentStats, r: Readiness, config: str) -> dict:
    return {
        "intent": s.intent,
        "config": config,
        "models": s.models,
        "total": s.total,
        "unanswered": s.unanswered,
        "scored": s.scored,
        "sendable_rate": rate(s.sendable, s.scored),
        "wrong_rate": rate(s.wrong, s.scored),
        "amount_conflicts_7d": s.amount_conflicts_7d,
        "handoff_extra": s.handoff_extra,
        "handoff_missing": s.handoff_missing,
        "median_bot_lead_s": s.median_bot_lead_s,
        "ready": r.ready,
        "not_ready_because": r.reasons,
    }


async def summary(s: AsyncSession, tenant_id: UUID, days: int) -> dict:
    cal = await calibration(s, tenant_id)
    rows = await s.execute(_SUMMARY, {"t": tenant_id, "d": days})
    intents = []
    for row in rows:
        st = IntentStats(
            intent=row.intent,
            total=row.total,
            unanswered=row.unanswered,
            scored=row.scored,
            sendable=row.sendable,
            wrong=row.wrong,
            amount_conflicts_7d=row.conflicts,
            handoff_extra=row.handoff_extra,
            handoff_missing=row.handoff_missing,
            median_bot_lead_s=row.median_lead,
            models=list(row.models),
        )
        intents.append(_json(st, readiness(st, judge_calibrated=cal.calibrated), row.config))
    return {"days": days, "judge_calibrated": cal.calibrated, "intents": intents}
