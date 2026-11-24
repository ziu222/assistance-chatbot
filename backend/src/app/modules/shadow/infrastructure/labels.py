"""Nhãn của chủ shop cho cặp đã chấm và mức hiệu chỉnh giám khảo (thiết kế 04 S3).

Nội dung (tin khách, nháp, câu nhân viên) đọc từ `messages` / `agent_drafts` lúc hiển thị, không
chép vào bảng so sánh. Hiệu chỉnh chỉ tính các cặp do chính bản prompt giám khảo hiện tại chấm:
đổi prompt thì phải gán nhãn lại.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.shadow.domain.calibration import Calibration, calibrate, judge_label
from app.modules.shadow.domain.judge import JUDGE_PROMPT_VERSION

_TEXTS = """
    (SELECT coalesce(string_agg(m.content, E'\\n' ORDER BY m.sequence), '') FROM messages m
      WHERE m.tenant_id = c.tenant_id AND m.conversation_id = c.conversation_id
        AND m.sender_type = 'customer' AND m.sequence BETWEEN r.from_sequence AND r.to_sequence)
      AS customer,
    (SELECT coalesce(string_agg(m.content, E'\\n' ORDER BY m.sequence), '') FROM messages m
      WHERE m.tenant_id = c.tenant_id AND m.conversation_id = c.conversation_id
        AND m.sender_type = 'human'
        AND m.sequence BETWEEN c.reference_from_seq AND c.reference_to_seq) AS staff
"""


async def list_for_labelling(
    s: AsyncSession, tenant_id: UUID, *, intent: str | None, unlabelled: bool, limit: int
) -> list[dict]:
    rows = await s.execute(
        text(
            "SELECT c.id, c.intent, c.final_verdict, c.sendable, c.judge, c.code_checks, "
            "c.created_at, d.text AS draft, l.label, " + _TEXTS + " "
            "FROM shadow_comparisons c "
            "JOIN agent_runs r ON r.tenant_id = c.tenant_id AND r.id = c.run_id "
            "JOIN agent_drafts d ON d.tenant_id = c.tenant_id AND d.id = c.draft_id "
            "LEFT JOIN judge_labels l ON l.comparison_id = c.id "
            "WHERE c.tenant_id = :t AND c.judged_at IS NOT NULL AND c.judge_prompt_version = :pv "
            "AND (CAST(:i AS text) IS NULL OR c.intent = :i) "
            "AND (NOT :unl OR l.label IS NULL) "
            "ORDER BY c.created_at DESC LIMIT :n"
        ),
        {"t": tenant_id, "pv": JUDGE_PROMPT_VERSION, "i": intent, "unl": unlabelled, "n": limit},
    )
    return [
        {
            "id": str(r.id),
            "intent": r.intent,
            "customer": r.customer,
            "draft": r.draft,
            "staff": r.staff,
            "judge": {
                "verdict": r.final_verdict,
                "sendable": r.sendable,
                "label": judge_label(r.sendable),
                "rationale": (r.judge or {}).get("rationale"),
            },
            "code_checks": r.code_checks,
            "label": r.label,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


async def set_label(
    s: AsyncSession, tenant_id: UUID, comparison_id: UUID, label: str, user_id: UUID
) -> bool:
    """Chỉ cặp do giám khảo chấm mới gán nhãn được (cặp code đã kết luận thì không cần)."""
    row = (
        await s.execute(
            text(
                "INSERT INTO judge_labels (comparison_id, tenant_id, label, labeled_by) "
                "SELECT c.id, c.tenant_id, :l, :u FROM shadow_comparisons c "
                "WHERE c.tenant_id = :t AND c.id = :id AND c.judged_at IS NOT NULL "
                "ON CONFLICT (comparison_id) DO UPDATE SET label = EXCLUDED.label, "
                "labeled_by = EXCLUDED.labeled_by, labeled_at = now() "
                "RETURNING comparison_id"
            ),
            {"t": tenant_id, "id": comparison_id, "l": label, "u": user_id},
        )
    ).first()
    return row is not None


async def calibration(s: AsyncSession, tenant_id: UUID) -> Calibration:
    rows = await s.execute(
        text(
            "SELECT c.sendable, l.label FROM judge_labels l "
            "JOIN shadow_comparisons c ON c.tenant_id = l.tenant_id AND c.id = l.comparison_id "
            "WHERE l.tenant_id = :t AND c.judge_prompt_version = :pv AND c.sendable IS NOT NULL"
        ),
        {"t": tenant_id, "pv": JUDGE_PROMPT_VERSION},
    )
    return calibrate([(judge_label(r.sendable), r.label) for r in rows])
