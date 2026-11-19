from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Customer:
    id: UUID
    tenant_id: UUID
    display_name: str
    status: str
    created_at: datetime


@dataclass(frozen=True)
class ChannelIdentity:
    id: UUID
    tenant_id: UUID
    customer_id: UUID
    channel_connection_id: UUID
    provider: str
    scoped_external_id: str
    first_seen_at: datetime
    last_inbound_at: datetime | None
