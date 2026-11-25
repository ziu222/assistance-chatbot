"""Nháp cho nhân viên ở chế độ Learning và lựa chọn của nhân viên (thiết kế 05 L1).

Chỉ đọc nháp và ghi `draft_uses`; tin nhắn do API trả lời của nhân viên ghi như mọi khi.
ponytail: `draft_uses` ghi ở transaction ngay sau tin nhắn (không chung transaction, vì use case
gửi tin nằm ở module `conversation`); khóa chính `draft_id` làm cho việc ghi lại an toàn.
"""

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.learning.domain.draft_use import compare
from app.shared.domain.text import extract_amounts
from app.shared.infrastructure.signals import record_signal

# Nháp hợp lệ: của agent đang ở Learning, đã qua kiểm tra, ứng với tin khách cuối, chưa ai trả lời
# sau nó, và nhân viên chưa chọn gì. `before_seq` loại trừ tin nhân viên vừa gửi (bỏ ngầm).
_VALID = """
    SELECT d.id, d.text, d.needs_human, d.reason, d.sources, d.based_on_sequence, d.created_at,
           d.shown_at
    FROM agent_drafts d
    JOIN conversations c ON c.tenant_id = d.tenant_id AND c.id = d.conversation_id
    JOIN agents a ON a.tenant_id = c.tenant_id AND a.channel_connection_id = c.channel_connection_id
    WHERE d.tenant_id = :t AND d.conversation_id = :c AND a.mode = 'learning' AND d.grounded
      AND NOT EXISTS (SELECT 1 FROM draft_uses u WHERE u.draft_id = d.id)
      AND NOT EXISTS (
        SELECT 1 FROM messages m WHERE m.tenant_id = d.tenant_id AND m.draft_id = d.id)
      AND d.based_on_sequence >= (
        SELECT coalesce(max(m.sequence), 0) FROM messages m
        WHERE m.tenant_id = d.tenant_id AND m.conversation_id = d.conversation_id
          AND m.sender_type = 'customer' AND m.sequence < :before)
      AND NOT EXISTS (
        SELECT 1 FROM messages m WHERE m.tenant_id = d.tenant_id
          AND m.conversation_id = d.conversation_id AND m.sender_type = 'human'
          AND m.sequence > d.based_on_sequence AND m.sequence < :before)
    ORDER BY d.based_on_sequence DESC, d.created_at DESC LIMIT 1
"""
NO_LIMIT = 2**62


async def current_draft(s: AsyncSession, tenant_id: UUID, conversation_id: UUID) -> dict | None:
    """Nháp để hiện trong ô soạn tin; ghi lại lần đầu nó được hiện (`shown_at`)."""
    row = (
        await s.execute(text(_VALID), {"t": tenant_id, "c": conversation_id, "before": NO_LIMIT})
    ).first()
    if row is None:
        return None
    if row.shown_at is None:
        await s.execute(
            text("UPDATE agent_drafts SET shown_at = now() WHERE tenant_id = :t AND id = :id"),
            {"t": tenant_id, "id": row.id},
        )
    return {
        "id": str(row.id),
        "text": row.text,
        "needs_human": row.needs_human,
        "reason": row.reason,
        "sources": row.sources,
        "amounts": sorted(extract_amounts(row.text)),  # để giao diện tô sáng
        "created_at": row.created_at.isoformat(),
    }


async def _draft_text(s: AsyncSession, tenant_id: UUID, conversation_id: UUID, draft_id: UUID):
    return (
        await s.execute(
            text(
                "SELECT text FROM agent_drafts WHERE tenant_id = :t AND conversation_id = :c "
                "AND id = :id"
            ),
            {"t": tenant_id, "c": conversation_id, "id": draft_id},
        )
    ).scalar_one_or_none()


async def record_use(
    s: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    draft_id: UUID,
    *,
    message_id: UUID | None,
    sent: str | None,
    used: bool,
    user_id: UUID,
) -> str | None:
    """Ghi lựa chọn của nhân viên. None nếu nháp không thuộc hội thoại này (hoặc đã ghi rồi)."""
    draft = await _draft_text(s, tenant_id, conversation_id, draft_id)
    if draft is None:
        return None
    use = compare(draft, sent, used=used)
    inserted = (
        await s.execute(
            text(
                "INSERT INTO draft_uses (draft_id, tenant_id, conversation_id, message_id, action, "
                "edit_ratio, diff_features, user_id) VALUES (:d, :t, :c, :m, :a, :r, "
                "CAST(:f AS jsonb), :u) ON CONFLICT (draft_id) DO NOTHING RETURNING draft_id"
            ),
            {
                "d": draft_id,
                "t": tenant_id,
                "c": conversation_id,
                "m": message_id,
                "a": use.action,
                "r": use.edit_ratio,
                "f": json.dumps(use.features()),
                "u": user_id,
            },
        )
    ).first()
    if inserted is None:
        return None
    await record_signal(
        s,
        tenant_id,
        conversation_id,
        f"draft_{'used' if use.action == 'as_is' else use.action}",
        {"draft_id": str(draft_id), "edit_ratio": use.edit_ratio, "minor": use.minor},
    )
    return use.action


async def discard_shown_draft(
    s: AsyncSession, tenant_id: UUID, conversation_id: UUID, message_id: UUID, sequence: int,
    user_id: UUID,
) -> str | None:  # fmt: skip
    """Nhân viên tự gõ câu khác trong khi nháp đang hiện ⇒ bỏ ngầm (không tính nháp chưa hiện)."""
    row = (
        await s.execute(text(_VALID), {"t": tenant_id, "c": conversation_id, "before": sequence})
    ).first()
    if row is None or row.shown_at is None:
        return None
    return await record_use(
        s, tenant_id, conversation_id, row.id, message_id=message_id, sent=None, used=False,
        user_id=user_id,
    )  # fmt: skip
