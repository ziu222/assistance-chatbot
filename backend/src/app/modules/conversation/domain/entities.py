from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Conversation:
    id: UUID
    tenant_id: UUID
    channel_connection_id: UUID
    customer_id: UUID
    status: str
    last_sequence: int
    last_message_at: datetime | None
    last_message_preview: str | None
    unread_count: int
    version: int
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True)
class Message:
    id: UUID
    tenant_id: UUID
    channel_connection_id: UUID
    conversation_id: UUID
    external_message_id: str
    direction: str
    sender_type: str
    sender_ref: str | None
    content: str
    status: str
    sequence: int
    occurred_at: datetime
    trace_id: str | None
