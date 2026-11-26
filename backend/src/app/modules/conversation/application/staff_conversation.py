"""Hành động của nhân viên trên một conversation: trả lời và đánh dấu đã đọc."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from app.modules.conversation.application.ports import ConversationUnitOfWork
from app.modules.conversation.application.receive_inbound_message import (
    EVENT_SCHEMA_VERSION,
    PREVIEW_LENGTH,
    ReceiveResult,
)
from app.modules.outbound.domain.channels import check_channel_send
from app.shared.application.errors import RetryableConflict
from app.shared.application.retry import retry_conflicts
from app.shared.domain.tenant import TenantContext


class ConversationNotFound(Exception):
    pass


class ConversationClosed(Exception):
    pass


class DuplicateClientMessageId(Exception):
    """client_message_id đã dùng cho tin ở conversation khác."""


class ChannelSendBlocked(Exception):
    """Kênh không cho phép gửi tin theo `ChannelCapabilities` / cửa sổ trả lời."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class SendStaffMessage:
    """Lưu tin trả lời (outbound/human) + outbox trong một transaction; idempotent.

    Lock conversation (FOR UPDATE) tuần tự hóa với tin inbound nên sequence không trùng/gap.
    Webchat không có provider ngoài: đã lưu và phát tín hiệu = đã gửi (status 'sent').
    """

    def __init__(self, uow_factory: Callable[[], ConversationUnitOfWork]) -> None:
        self._uow = uow_factory

    async def __call__(
        self,
        ctx: TenantContext,
        conversation_id: UUID,
        *,
        user_id: UUID,
        client_message_id: str,
        text: str,
        trace_id: str | None,
    ) -> ReceiveResult:
        args = (ctx, conversation_id, user_id, client_message_id, text, trace_id)
        return await retry_conflicts(lambda: self._attempt(*args))

    async def _attempt(
        self,
        ctx: TenantContext,
        conversation_id: UUID,
        user_id: UUID,
        client_message_id: str,
        text: str,
        trace_id: str | None,
    ) -> ReceiveResult:
        async with self._uow() as uow:
            conv = await uow.conversations.get(ctx, conversation_id, lock=True)
            if conv is None:
                raise ConversationNotFound

            # Sau khi giữ lock: bản trùng đã commit (nếu có) chắc chắn nhìn thấy.
            existing = await uow.messages.get_by_external_id(
                ctx, conv.channel_connection_id, "outbound", client_message_id
            )
            if existing is not None:
                if existing.conversation_id != conv.id:
                    raise DuplicateClientMessageId
                return ReceiveResult(existing.id, conv.id, existing.sequence, False)
            if conv.status != "open":
                raise ConversationClosed

            now = datetime.now(UTC)
            channel_state = await uow.conversations.channel_reply_state(ctx, conv.id)
            if channel_state is not None:
                decision = check_channel_send(
                    channel_state.provider,
                    last_customer_at=channel_state.last_customer_at,
                    now=now,
                    replies_since_customer=channel_state.outbound_since_customer,
                    config=channel_state.config,
                    text_length=len(text),
                )
                if not decision.allowed:
                    raise ChannelSendBlocked(decision.reason or "channel_blocked")

            sequence = conv.last_sequence + 1
            message = await uow.messages.add(
                ctx,
                conversation_id=conv.id,
                connection_id=conv.channel_connection_id,
                external_message_id=client_message_id,
                direction="outbound",
                sender_type="human",
                sender_ref=str(user_id),
                content=text,
                status="sent",
                sequence=sequence,
                occurred_at=now,
                trace_id=trace_id,
            )
            if message is None:
                raise RetryableConflict("duplicate outbound message not visible yet")

            await uow.conversations.record_message(
                ctx,
                conv.id,
                sequence=sequence,
                at=now,
                preview=text[:PREVIEW_LENGTH],
                inbound=False,
            )
            await uow.conversations.claim_handoff(ctx, conv.id, user_id)
            identity = await uow.customers.identity_for_customer(
                ctx, conv.customer_id, conv.channel_connection_id
            )
            await uow.outbox.add(
                ctx,
                aggregate_type="conversation",
                aggregate_id=conv.id,
                event_type="message.created",
                payload={  # chỉ ID, không nội dung (thiết kế §6.3)
                    "v": EVENT_SCHEMA_VERSION,
                    "event_type": "message.created",
                    "tenant_id": str(ctx.tenant_id),
                    "connection_id": str(conv.channel_connection_id),
                    "conversation_id": str(conv.id),
                    "customer_id": str(conv.customer_id),
                    "visitor_id": identity.scoped_external_id if identity else None,
                    "message_id": str(message.id),
                    "sequence": sequence,
                    "direction": "outbound",
                    "sender_type": "human",
                    "occurred_at": now.isoformat(),
                    "trace_id": trace_id,
                },
                trace_id=trace_id,
            )
            if channel_state is not None and channel_state.provider != "webchat" and identity:
                await uow.deliveries.enqueue(
                    ctx,
                    message_id=message.id,
                    connection_id=conv.channel_connection_id,
                    provider=channel_state.provider,
                    recipient_external_id=identity.scoped_external_id,
                    content=text,
                )
            await uow.commit()
            return ReceiveResult(message.id, conv.id, sequence, True)


class MarkConversationRead:
    def __init__(self, uow_factory: Callable[[], ConversationUnitOfWork]) -> None:
        self._uow = uow_factory

    async def __call__(
        self, ctx: TenantContext, conversation_id: UUID, *, trace_id: str | None
    ) -> None:
        async def attempt() -> None:
            async with self._uow() as uow:
                conv = await uow.conversations.get(ctx, conversation_id, lock=True)
                if conv is None:
                    raise ConversationNotFound
                if conv.unread_count == 0:
                    return  # đã đọc: không ghi, không phát event thừa
                await uow.conversations.mark_read(ctx, conv.id)
                await uow.outbox.add(
                    ctx,
                    aggregate_type="conversation",
                    aggregate_id=conv.id,
                    event_type="conversation.read",
                    payload={
                        "v": EVENT_SCHEMA_VERSION,
                        "event_type": "conversation.read",
                        "tenant_id": str(ctx.tenant_id),
                        "connection_id": str(conv.channel_connection_id),
                        "conversation_id": str(conv.id),
                        "trace_id": trace_id,
                    },
                    trace_id=trace_id,
                )
                await uow.commit()

        await retry_conflicts(attempt)
