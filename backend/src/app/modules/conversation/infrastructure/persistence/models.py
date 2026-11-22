import uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.infrastructure.db import Base


class ConversationModel(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    channel_connection_id: Mapped[uuid.UUID]
    customer_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(server_default="open")
    last_sequence: Mapped[int] = mapped_column(server_default="0")
    last_message_at: Mapped[datetime | None]
    last_message_preview: Mapped[str | None]
    unread_count: Mapped[int] = mapped_column(server_default="0")
    version: Mapped[int] = mapped_column(server_default="1")
    handling: Mapped[str] = mapped_column(server_default="bot")  # bot|waiting_human|human
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    closed_at: Mapped[datetime | None]


class MessageModel(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    channel_connection_id: Mapped[uuid.UUID]
    conversation_id: Mapped[uuid.UUID]
    external_message_id: Mapped[str]
    direction: Mapped[str]
    sender_type: Mapped[str]
    sender_ref: Mapped[str | None]
    message_type: Mapped[str] = mapped_column(server_default="text")
    content: Mapped[str]
    status: Mapped[str]
    sequence: Mapped[int]
    occurred_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    trace_id: Mapped[str | None]
