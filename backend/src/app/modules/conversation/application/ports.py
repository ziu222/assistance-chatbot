from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, Self
from uuid import UUID

from app.modules.conversation.domain.entities import Conversation, Message
from app.modules.customer.application.resolve_customer import CustomerStore
from app.shared.domain.tenant import TenantContext


@dataclass(frozen=True)
class ChannelReplyState:
    provider: str
    config: dict[str, Any]
    last_customer_at: datetime | None
    outbound_since_customer: int


class ConversationStore(Protocol):
    async def get(
        self, ctx: TenantContext, conversation_id: UUID, *, lock: bool = False
    ) -> Conversation | None: ...

    async def mark_read(self, ctx: TenantContext, conversation_id: UUID) -> None: ...

    async def find_open(
        self, ctx: TenantContext, customer_id: UUID, connection_id: UUID, *, lock: bool = False
    ) -> Conversation | None: ...

    async def add_if_no_open(
        self, ctx: TenantContext, customer_id: UUID, connection_id: UUID
    ) -> Conversation | None: ...

    async def record_message(
        self,
        ctx: TenantContext,
        conversation_id: UUID,
        *,
        sequence: int,
        at: datetime,
        preview: str,
        inbound: bool,
    ) -> None: ...

    async def claim_handoff(self, ctx: TenantContext, conversation_id: UUID, user_id: UUID) -> None:
        """Nhân viên trả lời ⇒ nhận luôn ticket đang mở của hội thoại (bước 2 §12.5)."""
        ...

    async def channel_reply_state(
        self, ctx: TenantContext, conversation_id: UUID
    ) -> ChannelReplyState | None: ...


class MessageStore(Protocol):
    async def add(self, ctx: TenantContext, **fields: Any) -> Message | None: ...

    async def get_by_external_id(
        self, ctx: TenantContext, connection_id: UUID, direction: str, external_message_id: str
    ) -> Message | None: ...


class OutboxPort(Protocol):
    async def add(
        self,
        ctx: TenantContext,
        *,
        aggregate_type: str,
        aggregate_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        trace_id: str | None,
    ) -> None: ...


class DeliveryPort(Protocol):
    async def enqueue(
        self,
        ctx: TenantContext,
        *,
        message_id: UUID,
        connection_id: UUID,
        provider: str,
        recipient_external_id: str,
        content: str,
    ) -> None: ...


class ConversationUnitOfWork(Protocol):
    """Một transaction. Thoát khối `async with` mà chưa commit() thì rollback.

    Xung đột tạm thời của DB (deadlock/serialization) được dịch thành RetryableConflict.
    """

    customers: CustomerStore
    conversations: ConversationStore
    messages: MessageStore
    outbox: OutboxPort
    deliveries: DeliveryPort

    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, *exc_info: object) -> None: ...

    async def commit(self) -> None: ...
