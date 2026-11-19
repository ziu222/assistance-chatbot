"""Helper dựng dữ liệu bằng SQL thô: cố ý không đi qua repository để kiểm chứng ràng buộc DB."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.domain.tenant import TenantContext


async def make_tenant(s: AsyncSession, slug: str | None = None) -> TenantContext:
    tid = uuid4()
    await s.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": tid, "name": "T", "slug": slug or f"t-{tid.hex[:8]}"},
    )
    return TenantContext(tid)


async def make_connection(s: AsyncSession, ctx: TenantContext) -> UUID:
    cid = uuid4()
    await s.execute(
        text(
            "INSERT INTO channel_connections (id, tenant_id, provider, public_key) "
            "VALUES (:id, :t, 'webchat', :pk)"
        ),
        {"id": cid, "t": ctx.tenant_id, "pk": f"wpk_{cid.hex[:12]}"},
    )
    return cid


async def make_customer(s: AsyncSession, ctx: TenantContext) -> UUID:
    cid = uuid4()
    await s.execute(
        text("INSERT INTO customers (id, tenant_id, display_name) VALUES (:id, :t, 'K')"),
        {"id": cid, "t": ctx.tenant_id},
    )
    return cid


async def make_conversation(
    s: AsyncSession,
    ctx: TenantContext,
    customer_id: UUID,
    connection_id: UUID,
    status: str = "open",
) -> UUID:
    cid = uuid4()
    await s.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, customer_id, channel_connection_id, status) "
            "VALUES (:id, :t, :cu, :co, :st)"
        ),
        {"id": cid, "t": ctx.tenant_id, "cu": customer_id, "co": connection_id, "st": status},
    )
    return cid


async def insert_message(
    s: AsyncSession,
    ctx: TenantContext,
    conversation_id: UUID,
    connection_id: UUID,
    *,
    external_id: str | None = None,
    sequence: int = 1,
    content: str = "xin chào",
    direction: str = "inbound",
) -> None:
    await s.execute(
        text(
            "INSERT INTO messages (id, tenant_id, channel_connection_id, conversation_id, "
            "external_message_id, direction, sender_type, content, status, sequence, occurred_at) "
            "VALUES (:id, :t, :co, :cv, :ext, :dir, 'customer', :content, 'received', :seq, :at)"
        ),
        {
            "id": uuid4(),
            "t": ctx.tenant_id,
            "co": connection_id,
            "cv": conversation_id,
            "ext": external_id or str(uuid4()),
            "dir": direction,
            "content": content,
            "seq": sequence,
            "at": datetime.now(UTC),
        },
    )
