from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from app.modules.conversation.application.ports import ConversationUnitOfWork
from app.modules.conversation.domain.entities import Conversation, Message
from app.modules.customer.application.resolve_customer import (
    ResolvedCustomer,
    resolve_customer_for_channel_identity,
)
from app.modules.integration.domain.channel_event import NormalizedChannelEvent
from app.shared.application.errors import RetryableConflict
from app.shared.application.retry import retry_conflicts
from app.shared.domain.tenant import TenantContext

PREVIEW_LENGTH = 120
EVENT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReceiveResult:
    message_id: UUID
    conversation_id: UUID
    sequence: int
    created: bool


def _result(message: Message, *, created: bool) -> ReceiveResult:
    return ReceiveResult(message.id, message.conversation_id, message.sequence, created)


class ReceiveInboundMessage:
    """Lưu một tin inbound đúng một lần, cấp sequence, ghi outbox — trong một transaction.

    Bất biến (được DB bảo đảm, không phải code):
    - idempotency: uq_message_idempotency
    - sequence liên tục theo conversation: row lock trên conversations
    - một conversation mở mỗi (customer, connection): uq_conversation_one_open

    Ghi chú: với tin inbound, UPDATE last_inbound_at trên channel_identities cũng khóa dòng
    và đã tuần tự hóa các tin của cùng một khách. Lock conversation vẫn bắt buộc: tin outbound
    của nhân viên (bước 1.6) không đi qua identity. Đã kiểm chứng: bỏ cả hai lock thì test
    sequence đồng thời fail với uq_message_sequence.
    """

    def __init__(self, uow_factory: Callable[[], ConversationUnitOfWork]) -> None:
        self._uow = uow_factory

    async def __call__(self, event: NormalizedChannelEvent) -> ReceiveResult:
        ctx = TenantContext(event.tenant_id)

        # Fast path: retry của client không cần mở transaction ghi.
        async with self._uow() as uow:
            existing = await uow.messages.get_by_external_id(
                ctx, event.channel_connection_id, "inbound", event.external_message_id
            )
        if existing is not None:
            return _result(existing, created=False)

        return await retry_conflicts(lambda: self._attempt(ctx, event))

    async def _attempt(self, ctx: TenantContext, event: NormalizedChannelEvent) -> ReceiveResult:
        async with self._uow() as uow:
            customer = await resolve_customer_for_channel_identity(
                uow.customers,
                ctx,
                event.channel_connection_id,
                event.provider,
                event.external_customer_id,
                event.occurred_at,
            )
            conversation = await self._open_conversation(uow, ctx, event, customer)

            sequence = conversation.last_sequence + 1
            message = await uow.messages.add(
                ctx,
                conversation_id=conversation.id,
                connection_id=event.channel_connection_id,
                external_message_id=event.external_message_id,
                direction="inbound",
                sender_type="customer",
                sender_ref=None,
                content=event.text,
                status="received",
                sequence=sequence,
                occurred_at=event.occurred_at,
                trace_id=event.trace_id,
            )
            if message is None:
                # Bản trùng đã commit trước (ta chờ nó ở row lock); thoát khối -> rollback.
                existing = await uow.messages.get_by_external_id(
                    ctx, event.channel_connection_id, "inbound", event.external_message_id
                )
                if existing is None:
                    raise RetryableConflict("duplicate message not visible yet")
                return _result(existing, created=False)

            await uow.conversations.record_message(
                ctx,
                conversation.id,
                sequence=sequence,
                at=event.occurred_at,
                preview=event.text[:PREVIEW_LENGTH],
                inbound=True,
            )
            await uow.outbox.add(
                ctx,
                aggregate_type="conversation",
                aggregate_id=conversation.id,
                event_type="message.created",
                payload=self._message_payload(event, customer, message),
                trace_id=event.trace_id,
            )
            await uow.commit()
            return _result(message, created=True)

    async def _open_conversation(
        self,
        uow: ConversationUnitOfWork,
        ctx: TenantContext,
        event: NormalizedChannelEvent,
        customer: ResolvedCustomer,
    ) -> Conversation:
        connection_id = event.channel_connection_id
        conversation = await uow.conversations.find_open(
            ctx, customer.customer_id, connection_id, lock=True
        )
        if conversation is not None:
            return conversation

        created = await uow.conversations.add_if_no_open(ctx, customer.customer_id, connection_id)
        if created is not None:
            await uow.outbox.add(
                ctx,
                aggregate_type="conversation",
                aggregate_id=created.id,
                event_type="conversation.created",
                payload={
                    "v": EVENT_SCHEMA_VERSION,
                    "event_type": "conversation.created",
                    "tenant_id": str(ctx.tenant_id),
                    "connection_id": str(connection_id),
                    "conversation_id": str(created.id),
                    "customer_id": str(customer.customer_id),
                    "visitor_id": event.external_customer_id,
                    "trace_id": event.trace_id,
                },
                trace_id=event.trace_id,
            )
            return created  # dòng do chính transaction này tạo, chưa ai thấy -> không cần lock

        # Bên khác vừa tạo và đã commit (ON CONFLICT chờ họ); khóa bản đó.
        conversation = await uow.conversations.find_open(
            ctx, customer.customer_id, connection_id, lock=True
        )
        if conversation is None:
            raise RetryableConflict("open conversation not visible yet")
        return conversation

    @staticmethod
    def _message_payload(
        event: NormalizedChannelEvent, customer: ResolvedCustomer, message: Message
    ) -> dict:
        # Chỉ ID: nội dung tin không đi qua outbox/Redis/log (thiết kế §6.3).
        return {
            "v": EVENT_SCHEMA_VERSION,
            "event_type": "message.created",
            "tenant_id": str(event.tenant_id),
            "connection_id": str(event.channel_connection_id),
            "conversation_id": str(message.conversation_id),
            "customer_id": str(customer.customer_id),
            "visitor_id": event.external_customer_id,
            "message_id": str(message.id),
            "sequence": message.sequence,
            "direction": "inbound",
            "sender_type": "customer",
            "occurred_at": event.occurred_at.isoformat(),
            "trace_id": event.trace_id,
        }
