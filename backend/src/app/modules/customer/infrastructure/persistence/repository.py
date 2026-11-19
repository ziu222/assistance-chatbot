from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customer.domain.entities import ChannelIdentity, Customer
from app.modules.customer.infrastructure.persistence.models import (
    ChannelIdentityModel,
    CustomerModel,
)
from app.shared.domain.tenant import TenantContext


def _customer(m: CustomerModel) -> Customer:
    return Customer(m.id, m.tenant_id, m.display_name, m.status, m.created_at)


def _identity(m: ChannelIdentityModel) -> ChannelIdentity:
    return ChannelIdentity(
        m.id,
        m.tenant_id,
        m.customer_id,
        m.channel_connection_id,
        m.provider,
        m.scoped_external_id,
        m.first_seen_at,
        m.last_inbound_at,
    )


class CustomerRepository:
    """Mọi truy vấn lọc theo ctx.tenant_id; tenant luôn do caller truyền qua TenantContext."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add_customer(self, ctx: TenantContext, display_name: str) -> Customer:
        m = CustomerModel(id=uuid4(), tenant_id=ctx.tenant_id, display_name=display_name)
        self._s.add(m)
        await self._s.flush()
        await self._s.refresh(m)
        return _customer(m)

    async def get_customer(self, ctx: TenantContext, customer_id: UUID) -> Customer | None:
        q = select(CustomerModel).where(
            CustomerModel.tenant_id == ctx.tenant_id, CustomerModel.id == customer_id
        )
        m = (await self._s.execute(q)).scalar_one_or_none()
        return _customer(m) if m else None

    async def add_identity(
        self,
        ctx: TenantContext,
        customer_id: UUID,
        connection_id: UUID,
        provider: str,
        scoped_external_id: str,
    ) -> ChannelIdentity:
        # Vi phạm unique/FK sẽ ném IntegrityError; use case (bước 1.3) quyết định xử lý race.
        m = ChannelIdentityModel(
            id=uuid4(),
            tenant_id=ctx.tenant_id,
            customer_id=customer_id,
            channel_connection_id=connection_id,
            provider=provider,
            scoped_external_id=scoped_external_id,
        )
        self._s.add(m)
        await self._s.flush()
        await self._s.refresh(m)
        return _identity(m)

    async def add_identity_if_absent(
        self,
        ctx: TenantContext,
        customer_id: UUID,
        connection_id: UUID,
        provider: str,
        scoped_external_id: str,
        at: datetime,
    ) -> ChannelIdentity | None:
        """None nếu identity đã tồn tại (thua race). Không làm hỏng transaction."""
        stmt = (
            pg_insert(ChannelIdentityModel)
            .values(
                id=uuid4(),
                tenant_id=ctx.tenant_id,
                customer_id=customer_id,
                channel_connection_id=connection_id,
                provider=provider,
                scoped_external_id=scoped_external_id,
                last_inbound_at=at,
            )
            .on_conflict_do_nothing(constraint="uq_channel_identity")
            .returning(ChannelIdentityModel)
        )
        m = (await self._s.execute(stmt)).scalar_one_or_none()
        return _identity(m) if m else None

    async def delete_customer(self, ctx: TenantContext, customer_id: UUID) -> None:
        await self._s.execute(
            delete(CustomerModel).where(
                CustomerModel.tenant_id == ctx.tenant_id, CustomerModel.id == customer_id
            )
        )

    async def touch_inbound(self, ctx: TenantContext, identity_id: UUID, at: datetime) -> None:
        await self._s.execute(
            update(ChannelIdentityModel)
            .where(
                ChannelIdentityModel.tenant_id == ctx.tenant_id,
                ChannelIdentityModel.id == identity_id,
            )
            .values(last_inbound_at=at)
        )

    async def identity_for_customer(
        self, ctx: TenantContext, customer_id: UUID, connection_id: UUID
    ) -> ChannelIdentity | None:
        """Định danh của khách trên một kênh (để gửi tín hiệu tới đúng visitor)."""
        q = (
            select(ChannelIdentityModel)
            .where(
                ChannelIdentityModel.tenant_id == ctx.tenant_id,
                ChannelIdentityModel.customer_id == customer_id,
                ChannelIdentityModel.channel_connection_id == connection_id,
            )
            .order_by(ChannelIdentityModel.first_seen_at)
            .limit(1)
        )
        m = (await self._s.execute(q)).scalar_one_or_none()
        return _identity(m) if m else None

    async def get_identity(
        self, ctx: TenantContext, connection_id: UUID, scoped_external_id: str
    ) -> ChannelIdentity | None:
        q = select(ChannelIdentityModel).where(
            ChannelIdentityModel.tenant_id == ctx.tenant_id,
            ChannelIdentityModel.channel_connection_id == connection_id,
            ChannelIdentityModel.scoped_external_id == scoped_external_id,
        )
        m = (await self._s.execute(q)).scalar_one_or_none()
        return _identity(m) if m else None
