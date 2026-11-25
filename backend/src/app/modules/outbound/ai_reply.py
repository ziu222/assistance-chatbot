"""Cổng gửi tin AI duy nhất (thiết kế 06 A1, ADR-0033, ADR-0039).

Chỉ file này được ghi `messages.sender_type = 'bot'` (kèm `draft_id`). `agent_runtime` không bao
giờ import module `outbound` (được khóa bằng `test_architecture.py`).

Mọi lần gửi đều khóa `FOR UPDATE` trên hàng `conversations`, kiểm lại ngay dưới khóa:
- tin đã gửi trước đó chưa (idempotent theo `ai:<draft_id>`),
- công tắc / ý định còn bật không (qua `recheck` nếu caller truyền vào),
- `handling = 'bot'` và hội thoại đang `open`,
- không có tin khách mới hơn `based_on_sequence` và nhân viên chưa trả lời sau đó,
- chưa vượt `max_bot_streak` tin bot liên tiếp,
- kênh được phép gửi.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversation.application.receive_inbound_message import (
    EVENT_SCHEMA_VERSION,
    PREVIEW_LENGTH,
)
from app.modules.outbound.domain.gate import AI_SEND_PROVIDERS, DEFAULT_MAX_BOT_STREAK

RecheckFn = Callable[[AsyncSession], Awaitable[str | None]]


@dataclass(frozen=True)
class SendAiResult:
    sent: bool
    blocked_reason: str | None = None
    duplicate: bool = False
    message_id: UUID | None = None
    sequence: int | None = None


async def send_ai_reply(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    draft_id: UUID,
    run_id: UUID,
    based_on_sequence: int,
    content: str,
    max_bot_streak: int = DEFAULT_MAX_BOT_STREAK,
    channel_allowed: bool = True,
    recheck: RecheckFn | None = None,
) -> SendAiResult:
    """Khóa hội thoại, kiểm lại mọi điều kiện tranh chấp rồi ghi tin `bot` + outbox.

    Chạy trong transaction của người gọi. Idempotent theo `external_message_id = ai:<draft_id>`:
    worker chạy lại không bao giờ gửi hai lần.
    """
    conv = (
        await s.execute(
            text(
                "SELECT c.channel_connection_id, c.customer_id, c.status, c.handling, "
                "       c.last_sequence, cc.provider, ci.scoped_external_id "
                "FROM conversations c "
                "JOIN channel_connections cc "
                "  ON cc.tenant_id = c.tenant_id AND cc.id = c.channel_connection_id "
                "LEFT JOIN channel_identities ci "
                "  ON ci.tenant_id = c.tenant_id AND ci.customer_id = c.customer_id "
                " AND ci.channel_connection_id = c.channel_connection_id "
                "WHERE c.tenant_id = :t AND c.id = :c "
                "FOR UPDATE OF c"
            ),
            {"t": tenant_id, "c": conversation_id},
        )
    ).first()
    if conv is None:
        return SendAiResult(sent=False, blocked_reason="handling")

    connection_id, customer_id, status, handling, last_sequence, provider, visitor_id = conv
    ext_id = f"ai:{draft_id}"

    # 1. Idempotent trước tiên: nếu worker chạy lại cho nháp đã gửi thì không gửi thêm.
    existing = (
        await s.execute(
            text(
                "SELECT id, sequence FROM messages "
                "WHERE tenant_id = :t AND channel_connection_id = :conn "
                "  AND external_message_id = :ext"
            ),
            {"t": tenant_id, "conn": connection_id, "ext": ext_id},
        )
    ).first()
    if existing is not None:
        return SendAiResult(
            sent=False, duplicate=True, message_id=existing.id, sequence=existing.sequence
        )

    # 2. Kiểm lại công tắc / trạng thái ý định ngay dưới khóa hội thoại (chặn tranh chấp).
    if recheck is not None:
        reason = await recheck(s)
        if reason is not None:
            return SendAiResult(sent=False, blocked_reason=reason)

    # 3. Kiểm lại handling và trạng thái hội thoại dưới khóa.
    if handling != "bot" or status != "open":
        return SendAiResult(sent=False, blocked_reason="handling")

    # 4. Kiểm lại tin mới của khách và việc nhân viên đã trả lời hay chưa dưới khóa.
    latest_customer_seq = (
        await s.execute(
            text(
                "SELECT coalesce(max(sequence), 0) FROM messages "
                "WHERE tenant_id = :t AND conversation_id = :c AND sender_type = 'customer'"
            ),
            {"t": tenant_id, "c": conversation_id},
        )
    ).scalar_one()
    if based_on_sequence <= 0 or latest_customer_seq > based_on_sequence:
        return SendAiResult(sent=False, blocked_reason="stale")

    human_after = (
        await s.execute(
            text(
                "SELECT 1 FROM messages "
                "WHERE tenant_id = :t AND conversation_id = :c "
                "  AND sender_type = 'human' AND sequence > :seq LIMIT 1"
            ),
            {"t": tenant_id, "c": conversation_id, "seq": based_on_sequence},
        )
    ).first()
    if human_after is not None:
        return SendAiResult(sent=False, blocked_reason="stale")

    # 5. Kiểm số tin bot liên tiếp kể từ tin khách cuối cùng.
    bot_streak = (
        await s.execute(
            text(
                "SELECT count(*) FROM messages "
                "WHERE tenant_id = :t AND conversation_id = :c "
                "  AND sender_type = 'bot' AND sequence > :last_cust"
            ),
            {"t": tenant_id, "c": conversation_id, "last_cust": latest_customer_seq},
        )
    ).scalar_one()
    if bot_streak >= max_bot_streak:
        return SendAiResult(sent=False, blocked_reason="bot_streak")

    # 6. Kiểm kênh được phép gửi.
    if provider not in AI_SEND_PROVIDERS or not channel_allowed:
        return SendAiResult(sent=False, blocked_reason="channel")

    sequence, now, message_id = last_sequence + 1, datetime.now(UTC), uuid4()
    inserted = (
        await s.execute(
            text(
                "INSERT INTO messages (id, tenant_id, channel_connection_id, conversation_id, "
                "external_message_id, direction, sender_type, sender_ref, content, status, "
                "sequence, occurred_at, draft_id) "
                "VALUES (:id, :t, :conn, :c, :ext, 'outbound', 'bot', :ref, :content, 'sent', "
                ":seq, :at, :draft_id) "
                "ON CONFLICT ON CONSTRAINT uq_message_idempotency DO NOTHING RETURNING id"
            ),
            {
                "id": message_id,
                "t": tenant_id,
                "conn": connection_id,
                "c": conversation_id,
                "ext": ext_id,
                "ref": f"ai:{run_id}",
                "content": content,
                "seq": sequence,
                "at": now,
                "draft_id": draft_id,
            },
        )
    ).first()
    if inserted is None:
        return SendAiResult(sent=False, duplicate=True)

    # Không đổi `handling`, không tăng `unread_count`: đây là tin bot, không phải tin nhân viên.
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
    payload = {
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
        "sender_type": "bot",
        "draft_id": str(draft_id),
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
    return SendAiResult(sent=True, message_id=message_id, sequence=sequence)
