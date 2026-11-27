"""Cổng gửi duy nhất cho tin tự động (thiết kế bước 2 §12.6, ADR-0033).

Chỉ module này được ghi tin `sender_type='system'` vào `messages`. Bước 2.9 chỉ mở cổng cho tin
**do chủ shop viết** (nội dung nguyên văn từ `policy_release`, đã thay biến). Output của LLM
không đi qua đây; test kiến trúc chặn `agent_runtime` import module này.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversation.application.receive_inbound_message import (
    EVENT_SCHEMA_VERSION,
    PREVIEW_LENGTH,
)
from app.modules.outbound.infrastructure.deliveries import enqueue_delivery

# Kênh gửi chủ động được. ponytail: chỉ webchat; Messenger/Zalo cần kiểm cửa sổ 24 giờ trước.
PROACTIVE_PROVIDERS = frozenset({"webchat"})


@dataclass(frozen=True)
class GateInput:
    auto_send: bool  # công tắc của shop, mặc định tắt
    provider: str
    conversation_status: str
    handling_before: str  # bot phải đang giữ hội thoại lúc quyết định
    allow_waiting_human: bool = False  # chỉ mở cho tin `handoff_ack` ngay lúc vừa mở ticket


def send_block(g: GateInput) -> str | None:
    """None ⇒ được gửi; ngược lại là lý do bị chặn (hiện trong trace)."""
    if not g.auto_send:
        return "auto_send_off"
    if g.conversation_status != "open":
        return "conversation_closed"
    if g.handling_before != "bot" and not (
        g.allow_waiting_human and g.handling_before == "waiting_human"
    ):
        return "human_handling"
    if g.provider not in PROACTIVE_PROVIDERS:
        return "channel_not_supported"
    return None


async def send_canned(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    to_sequence: int,
    situation: str,
    release_id: UUID,
    content: str,
    external_message_id: str | None = None,
) -> bool:
    """Ghi tin tự động + outbox trong transaction của người gọi. Người gọi PHẢI đang giữ khóa
    `FOR UPDATE` trên hội thoại (tầng chính sách giữ sẵn) để sequence không trùng/gap.

    Idempotent theo cụm tin: `external_message_id = policy:<conv>:<to_sequence>`. Trả True nếu
    vừa ghi, False nếu đã có (worker retry).
    """
    conv = (
        await s.execute(
            text(
                "SELECT c.channel_connection_id, c.customer_id, c.last_sequence, "
                "cc.provider, ci.scoped_external_id FROM conversations c "
                "JOIN channel_connections cc ON cc.tenant_id = c.tenant_id "
                "  AND cc.id = c.channel_connection_id "
                "LEFT JOIN channel_identities ci ON ci.tenant_id = c.tenant_id "
                "  AND ci.customer_id = c.customer_id "
                "  AND ci.channel_connection_id = c.channel_connection_id "
                "WHERE c.tenant_id = :t AND c.id = :c"
            ),
            {"t": tenant_id, "c": conversation_id},
        )
    ).one()
    connection_id, customer_id, last_sequence, provider, visitor_id = conv
    sequence, now, message_id = last_sequence + 1, datetime.now(UTC), uuid4()
    inserted = (
        await s.execute(
            text(
                "INSERT INTO messages (id, tenant_id, channel_connection_id, conversation_id, "
                "external_message_id, direction, sender_type, sender_ref, content, status, "
                "sequence, occurred_at) VALUES (:id, :t, :conn, :c, :ext, 'outbound', 'system', "
                ":ref, :content, 'sent', :seq, :at) "
                "ON CONFLICT ON CONSTRAINT uq_message_idempotency DO NOTHING RETURNING id"
            ),
            {
                "id": message_id,
                "t": tenant_id,
                "conn": connection_id,
                "c": conversation_id,
                "ext": external_message_id or f"policy:{conversation_id}:{to_sequence}",
                "ref": f"policy:{situation}:{release_id}",
                "content": content,
                "seq": sequence,
                "at": now,
            },
        )
    ).first()
    if inserted is None:
        return False
    # Không đổi `handling`, không tăng `unread_count`: đây không phải tin của nhân viên.
    await s.execute(
        text(
            "UPDATE conversations SET last_sequence = :seq, last_message_at = :at, "
            "last_message_preview = :p, version = version + 1, updated_at = now() "
            "WHERE tenant_id = :t AND id = :c"
        ),
        {
            "seq": sequence,
            "at": now,
            "p": content[:PREVIEW_LENGTH],
            "t": tenant_id,
            "c": conversation_id,
        },
    )
    payload = {  # cùng dạng với tin của nhân viên: widget nhận tín hiệu và tải tin
        "v": EVENT_SCHEMA_VERSION,
        "event_type": "message.created",
        "tenant_id": str(tenant_id),
        "connection_id": str(connection_id),
        "conversation_id": str(conversation_id),
        "customer_id": str(customer_id),
        "visitor_id": visitor_id,
        "message_id": str(message_id),
        "sequence": sequence,
        "direction": "outbound",
        "sender_type": "system",
        "occurred_at": now.isoformat(),
        "trace_id": None,
    }
    await s.execute(
        text(
            "INSERT INTO outbox_events (tenant_id, aggregate_type, aggregate_id, event_type, "
            "payload) VALUES (:t, 'conversation', :c, 'message.created', CAST(:p AS jsonb))"
        ),
        {"t": tenant_id, "c": conversation_id, "p": json.dumps(payload)},
    )
    if provider != "webchat" and visitor_id:
        await enqueue_delivery(
            s,
            tenant_id=tenant_id,
            message_id=message_id,
            connection_id=connection_id,
            provider=provider,
            recipient_external_id=visitor_id,
            content=content,
        )
    return True
