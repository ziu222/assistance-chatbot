"""Public application interface của module customer (module khác chỉ gọi qua đây)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.modules.customer.domain.entities import ChannelIdentity, Customer
from app.shared.domain.tenant import TenantContext


class CustomerStore(Protocol):
    async def get_identity(
        self, ctx: TenantContext, connection_id: UUID, scoped_external_id: str
    ) -> ChannelIdentity | None: ...

    async def add_customer(self, ctx: TenantContext, display_name: str) -> Customer: ...

    async def add_identity_if_absent(
        self,
        ctx: TenantContext,
        customer_id: UUID,
        connection_id: UUID,
        provider: str,
        scoped_external_id: str,
        at: datetime,
    ) -> ChannelIdentity | None: ...

    async def delete_customer(self, ctx: TenantContext, customer_id: UUID) -> None: ...

    async def touch_inbound(self, ctx: TenantContext, identity_id: UUID, at: datetime) -> None: ...

    async def identity_for_customer(
        self, ctx: TenantContext, customer_id: UUID, connection_id: UUID
    ) -> ChannelIdentity | None: ...


@dataclass(frozen=True)
class ResolvedCustomer:
    customer_id: UUID
    identity_id: UUID
    created: bool


def anonymous_display_name(scoped_external_id: str) -> str:
    prefix = "".join(c for c in scoped_external_id if c.isalnum())[:4].upper()
    return f"Khách #{prefix or '????'}"


async def resolve_customer_for_channel_identity(
    store: CustomerStore,
    ctx: TenantContext,
    connection_id: UUID,
    provider: str,
    scoped_external_id: str,
    at: datetime,
) -> ResolvedCustomer:
    """Tìm hoặc tạo Customer + ChannelIdentity; an toàn khi hai request đầu tới cùng lúc.

    Phải chạy trong transaction của caller để customer/identity commit cùng message.
    """
    identity = await store.get_identity(ctx, connection_id, scoped_external_id)
    if identity is None:
        customer = await store.add_customer(ctx, anonymous_display_name(scoped_external_id))
        identity = await store.add_identity_if_absent(
            ctx, customer.id, connection_id, provider, scoped_external_id, at
        )
        if identity is not None:
            return ResolvedCustomer(customer.id, identity.id, created=True)
        # Thua race: INSERT ... ON CONFLICT đã chờ bên thắng commit, giờ đọc được bản của họ.
        await store.delete_customer(ctx, customer.id)
        identity = await store.get_identity(ctx, connection_id, scoped_external_id)
        if identity is None:  # pragma: no cover - chỉ xảy ra nếu bên thắng bị xóa giữa chừng
            raise RuntimeError("channel identity disappeared during resolution")

    await store.touch_inbound(ctx, identity.id, at)
    return ResolvedCustomer(identity.customer_id, identity.id, created=False)
