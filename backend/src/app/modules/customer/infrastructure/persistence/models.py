import uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.infrastructure.db import Base


class CustomerModel(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    display_name: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="active")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ChannelIdentityModel(Base):
    __tablename__ = "channel_identities"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    customer_id: Mapped[uuid.UUID]
    channel_connection_id: Mapped[uuid.UUID]
    provider: Mapped[str]
    scoped_external_id: Mapped[str]
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_inbound_at: Mapped[datetime | None]
