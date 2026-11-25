"""Tổng hợp so sánh shadow theo ý định và mức sẵn sàng (thiết kế 04 S4 & 05 §7 S5).

Tách riêng hai nguồn đo:
- **mù** (`draft_exposure = 'blind'`, Shadow): chấm bằng code + giám khảo đã hiệu chỉnh;
- **đã thấy** (`draft_exposure = 'shown'`, Learning): đo trực tiếp theo mức sửa (`draft_uses`).

Mỗi ý định chỉ tính các cặp thuộc phiên bản cấu hình AI HIỆN TẠI của nó (template, phiên bản
template, `PROMPT_VERSION`, `knowledge_hash`).
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.shadow.domain.readiness import (
    SHOWN_MINOR_EDIT_MAX,
    IntentStats,
    MeasurementSource,
    Readiness,
    classify_shown_use,
    rate,
    readiness,
)
from app.modules.shadow.infrastructure.labels import calibration

_SUMMARY = text(
    """
    WITH latest AS (
      SELECT DISTINCT ON (intent) intent, config_version - 'model' AS key
      FROM shadow_comparisons
      WHERE tenant_id = :t AND draft_exposure = 'blind'
      ORDER BY intent, created_at DESC
    ), c AS (
      SELECT sc.*, l.key FROM shadow_comparisons sc
      JOIN latest l ON sc.intent IS NOT DISTINCT FROM l.intent
       AND (sc.config_version - 'model') = l.key
      WHERE sc.tenant_id = :t AND sc.draft_exposure = 'blind'
        AND sc.created_at > now() - make_interval(days => :d)
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

_SHOWN_ROWS = text(
    """
    WITH latest AS (
      SELECT DISTINCT ON (intent) intent, config_version - 'model' AS key
      FROM shadow_comparisons
      WHERE tenant_id = :t AND draft_exposure = 'shown'
      ORDER BY intent, created_at DESC
    )
    SELECT sc.intent, l.key::text AS config, sc.config_version->>'model' AS model,
           sc.status, sc.code_checks, sc.bot_lead_seconds, sc.created_at,
           u.action, u.edit_ratio, u.diff_features
    FROM shadow_comparisons sc
    JOIN latest l ON sc.intent IS NOT DISTINCT FROM l.intent
     AND (sc.config_version - 'model') = l.key
    LEFT JOIN draft_uses u ON u.tenant_id = sc.tenant_id AND u.draft_id = sc.draft_id
    WHERE sc.tenant_id = :t AND sc.draft_exposure = 'shown'
      AND sc.created_at > now() - make_interval(days => :d)
    """
)


def _json(s: IntentStats, r: Readiness, config: str, *, source: MeasurementSource) -> dict:
    return {
        "intent": s.intent,
        "source": source,
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


async def _shown_summary(s: AsyncSession, tenant_id: UUID, days: int) -> list[dict]:
    rows = (await s.execute(_SHOWN_ROWS, {"t": tenant_id, "d": days})).all()
    by_intent: dict[str | None, list] = defaultdict(list)
    for r in rows:
        by_intent[r.intent].append(r)

    cutoff_7d = datetime.now(UTC) - timedelta(days=7)
    out: list[dict] = []
    for intent, group in sorted(by_intent.items(), key=lambda kv: -len(kv[1])):
        config = group[0].config
        models = sorted({r.model for r in group if r.model})
        unanswered = sum(1 for r in group if r.status == "unanswered")
        scored = sendable = wrong = conflicts_7d = 0
        as_is = minor_edit = discarded = 0
        leads: list[float] = []
        for r in group:
            if r.bot_lead_seconds is not None:
                leads.append(float(r.bot_lead_seconds))
            checks = r.code_checks or {}
            conflict = checks.get("amounts") == "conflict" and not bool(
                checks.get("reference_suspect")
            )
            if conflict and r.created_at >= cutoff_7d:
                conflicts_7d += 1
            if r.action is None:
                continue
            scored += 1
            if r.action == "as_is":
                as_is += 1
            elif r.action == "discarded":
                discarded += 1
            elif r.action == "edited" and float(r.edit_ratio) < SHOWN_MINOR_EDIT_MAX:
                minor_edit += 1
            grade = classify_shown_use(
                r.action, float(r.edit_ratio), r.diff_features, amount_conflict=conflict
            )
            if grade.sendable:
                sendable += 1
            if grade.wrong:
                wrong += 1

        st = IntentStats(
            intent=intent,
            total=len(group),
            unanswered=unanswered,
            scored=scored,
            sendable=sendable,
            wrong=wrong,
            amount_conflicts_7d=conflicts_7d,
            handoff_extra=0,
            handoff_missing=0,
            median_bot_lead_s=median(leads) if leads else None,
            models=models,
        )
        r_obj = readiness(st, judge_calibrated=True, source="shown")
        out.append(
            _json(st, r_obj, config, source="shown")
            | {
                "as_is_rate": rate(as_is, scored),
                "minor_edit_rate": rate(minor_edit, scored),
                "discarded_rate": rate(discarded, scored),
            }
        )
    return out


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
        intents.append(
            _json(
                st,
                readiness(st, judge_calibrated=cal.calibrated, source="blind"),
                row.config,
                source="blind",
            )
        )
    learning_intents = await _shown_summary(s, tenant_id, days)
    return {
        "days": days,
        "judge_calibrated": cal.calibrated,
        "intents": intents,
        "learning_intents": learning_intents,
    }
