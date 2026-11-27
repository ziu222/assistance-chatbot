"""Ticket "Nhờ nhân viên" (thiết kế bước 2 §12.5): SQL thẳng, mọi câu lọc tenant_id.

Mở ticket, đổi `handling`, outbox và tín hiệu học nằm trong CÙNG transaction của người gọi.
"""

import json
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.infrastructure.signals import record_signal

Source = Literal["customer_button", "phrase", "topic", "staff", "order"]
Action = Literal["claim", "resolve", "dismiss"]
EVENT_SCHEMA_VERSION = 1


async def _outbox(
    s: AsyncSession, tenant_id: UUID, conversation_id: UUID, event_type: str, handoff_id: UUID
) -> None:
    # Chỉ ID (như message.created). Không có visitor_id ⇒ chỉ phát tới kênh nhân viên của tenant.
    payload = {
        "v": EVENT_SCHEMA_VERSION,
        "event_type": event_type,
        "tenant_id": str(tenant_id),
        "conversation_id": str(conversation_id),
        "handoff_id": str(handoff_id),
    }
    await s.execute(
        text(
            "INSERT INTO outbox_events (tenant_id, aggregate_type, aggregate_id, event_type, "
            "payload) VALUES (:t, 'conversation', :c, :e, CAST(:p AS jsonb))"
        ),
        {"t": tenant_id, "c": conversation_id, "e": event_type, "p": json.dumps(payload)},
    )


async def open_handoff(
    s: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    *,
    source: Source,
    reason: str | None = None,
    priority: int = 0,
) -> tuple[UUID, bool]:
    """Mở ticket nếu hội thoại chưa có ticket đang hoạt động. Trả (id, mới_tạo).

    Partial unique index bảo đảm một ticket mỗi hội thoại kể cả khi hai yêu cầu chạy song song.
    Hội thoại đang `bot` ⇒ `waiting_human`; nhân viên đang xử lý (`human`) thì giữ nguyên.
    """
    row = (
        await s.execute(
            text(
                "INSERT INTO handoff_requests "
                "(id, tenant_id, conversation_id, source, reason, priority) "
                "VALUES (:id, :t, :c, :src, :r, :p) "
                "ON CONFLICT (tenant_id, conversation_id) WHERE status IN ('open','claimed') "
                "DO NOTHING RETURNING id"
            ),
            {
                "id": uuid4(),
                "t": tenant_id,
                "c": conversation_id,
                "src": source,
                "r": reason,
                "p": priority,
            },
        )
    ).first()
    if row is None:
        existing = (
            await s.execute(
                text(
                    "SELECT id FROM handoff_requests WHERE tenant_id = :t AND conversation_id = :c "
                    "AND status IN ('open','claimed')"
                ),
                {"t": tenant_id, "c": conversation_id},
            )
        ).scalar_one()
        return existing, False
    handoff_id = row[0]
    await s.execute(
        text(
            "UPDATE conversations SET handling = 'waiting_human', version = version + 1, "
            "updated_at = now() WHERE tenant_id = :t AND id = :c AND handling = 'bot'"
        ),
        {"t": tenant_id, "c": conversation_id},
    )
    await _outbox(s, tenant_id, conversation_id, "handoff.opened", handoff_id)
    await record_signal(
        s,
        tenant_id,
        conversation_id,
        "handoff_opened",
        {"handoff_id": str(handoff_id), "source": source},
    )
    return handoff_id, True


async def bump_priority(
    s: AsyncSession, tenant_id: UUID, conversation_id: UUID, priority: int
) -> None:
    """Chỉ tăng mức ưu tiên của ticket đang mở khi tín hiệu leo thang cao hơn."""
    if priority <= 0:
        return
    await s.execute(
        text(
            "UPDATE handoff_requests SET priority = GREATEST(priority, :p) "
            "WHERE tenant_id = :t AND conversation_id = :c AND status IN ('open', 'claimed')"
        ),
        {"p": priority, "t": tenant_id, "c": conversation_id},
    )


_TRANSITIONS: dict[Action, tuple[str, str]] = {
    # action: (trạng thái cho phép trước, SET ...)
    "claim": ("'open'", "status = 'claimed', claimed_by = :u, claimed_at = now()"),
    "resolve": ("'open','claimed'", "status = 'resolved', closed_at = now()"),
    "dismiss": ("'open','claimed'", "status = 'dismissed', not_needed = true, closed_at = now()"),
}


async def transition(
    s: AsyncSession, tenant_id: UUID, handoff_id: UUID, action: Action, user_id: UUID
) -> bool:
    """Nhận / xong / không cần. False nếu ticket không tồn tại hoặc không ở trạng thái hợp lệ."""
    allowed, assignment = _TRANSITIONS[action]
    row = (
        await s.execute(
            text(
                f"UPDATE handoff_requests SET {assignment} "  # noqa: S608 - hằng số trong code
                f"WHERE tenant_id = :t AND id = :id AND status IN ({allowed}) "
                "RETURNING conversation_id"
            ),
            {"t": tenant_id, "id": handoff_id, "u": user_id},
        )
    ).first()
    if row is None:
        return False
    conversation_id = row[0]
    if action == "claim":  # người nhận ⇒ bot im
        new_handling, only_from = "human", ("bot", "waiting_human")
    elif action == "dismiss":  # không cần người ⇒ bot tư vấn lại nếu chưa ai trả lời
        new_handling, only_from = "bot", ("waiting_human",)
    else:
        new_handling, only_from = None, ()
    if new_handling:
        await s.execute(
            text(
                "UPDATE conversations SET handling = :h, version = version + 1, updated_at = now() "
                "WHERE tenant_id = :t AND id = :c AND handling = ANY(:from)"
            ),
            {"h": new_handling, "t": tenant_id, "c": conversation_id, "from": list(only_from)},
        )
    await _outbox(s, tenant_id, conversation_id, "handoff.updated", handoff_id)
    if action != "claim":
        kind = "handoff_not_needed" if action == "dismiss" else "handoff_resolved"
        await record_signal(s, tenant_id, conversation_id, kind, {"handoff_id": str(handoff_id)})
    return True


@dataclass(frozen=True)
class Ticket:
    id: UUID
    conversation_id: UUID
    status: str
    source: str
    reason: str | None
    priority: int
    opened_at: Any
    claimed_by: UUID | None
    customer_name: str | None
    last_message_preview: str | None
    handling: str
    summary: str | None = None
    summary_status: str = "pending"


async def list_active(s: AsyncSession, tenant_id: UUID, limit: int = 100) -> list[Ticket]:
    # ponytail: một trang tối đa 100 ticket đang mở; thêm cursor khi shop có hàng đợi dài hơn.
    rows = await s.execute(
        text(
            "SELECT h.id, h.conversation_id, h.status, h.source, h.reason, h.priority, "
            "h.opened_at, h.claimed_by, cu.display_name, c.last_message_preview, c.handling, "
            "h.summary, h.summary_status "
            "FROM handoff_requests h "
            "JOIN conversations c ON c.tenant_id = h.tenant_id AND c.id = h.conversation_id "
            "JOIN customers cu ON cu.tenant_id = c.tenant_id AND cu.id = c.customer_id "
            "WHERE h.tenant_id = :t AND h.status IN ('open','claimed') "
            "ORDER BY h.priority DESC, h.opened_at LIMIT :n"
        ),
        {"t": tenant_id, "n": limit},
    )
    return [Ticket(*r) for r in rows]
