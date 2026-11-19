from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.conversation.infrastructure.persistence.repository import (
    ConversationRepository,
    MessageRepository,
)
from app.modules.customer.infrastructure.persistence.repository import CustomerRepository

from .factories import make_connection, make_tenant


def _msg(conv_id, conn_id, *, external="m1", sequence=1, content="xin chào"):
    return dict(
        conversation_id=conv_id,
        connection_id=conn_id,
        external_message_id=external,
        direction="inbound",
        sender_type="customer",
        sender_ref=None,
        content=content,
        status="received",
        sequence=sequence,
        occurred_at=datetime.now(UTC),
        trace_id=None,
    )


async def test_customer_and_identity_roundtrip(session_factory):
    async with session_factory() as s:
        ctx = await make_tenant(s)
        conn = await make_connection(s, ctx)
        repo = CustomerRepository(s)
        customer = await repo.add_customer(ctx, "Khách #A1B2")
        identity = await repo.add_identity(ctx, customer.id, conn, "webchat", "visitor-1")
        found = await repo.get_identity(ctx, conn, "visitor-1")
        assert found == identity
        assert (await repo.get_customer(ctx, customer.id)).display_name == "Khách #A1B2"


async def test_repositories_do_not_leak_across_tenants(session_factory):
    async with session_factory() as s:
        a, b = await make_tenant(s), await make_tenant(s)
        conn_a = await make_connection(s, a)
        customers = CustomerRepository(s)
        convs = ConversationRepository(s)
        msgs = MessageRepository(s)

        customer = await customers.add_customer(a, "K")
        await customers.add_identity(a, customer.id, conn_a, "webchat", "v1")
        conv = await convs.add(a, customer.id, conn_a)
        await msgs.add(a, **_msg(conv.id, conn_a))

        # tenant B không thấy gì của tenant A
        assert await customers.get_customer(b, customer.id) is None
        assert await customers.get_identity(b, conn_a, "v1") is None
        assert await convs.get(b, conv.id) is None
        assert await convs.find_open(b, customer.id, conn_a) is None
        assert await msgs.get_by_external_id(b, conn_a, "inbound", "m1") is None
        assert await msgs.list_after(b, conv.id) == []

        # tenant A thấy dữ liệu của mình
        assert (await convs.get(a, conv.id)).id == conv.id
        assert (await convs.find_open(a, customer.id, conn_a)).id == conv.id
        assert len(await msgs.list_after(a, conv.id)) == 1


async def test_message_add_returns_none_on_duplicate_idempotency_key(session_factory):
    async with session_factory() as s:
        ctx = await make_tenant(s)
        conn = await make_connection(s, ctx)
        customer = await CustomerRepository(s).add_customer(ctx, "K")
        conv = await ConversationRepository(s).add(ctx, customer.id, conn)
        msgs = MessageRepository(s)

        first = await msgs.add(ctx, **_msg(conv.id, conn, external="dup", sequence=1))
        second = await msgs.add(ctx, **_msg(conv.id, conn, external="dup", sequence=2))

        assert first is not None
        assert second is None
        assert len(await msgs.list_after(ctx, conv.id)) == 1


async def test_message_add_still_raises_on_other_violations(session_factory):
    async with session_factory() as s:
        ctx = await make_tenant(s)
        conn = await make_connection(s, ctx)
        customer = await CustomerRepository(s).add_customer(ctx, "K")
        conv = await ConversationRepository(s).add(ctx, customer.id, conn)
        msgs = MessageRepository(s)

        await msgs.add(ctx, **_msg(conv.id, conn, external="a", sequence=1))
        with pytest.raises(IntegrityError, match="uq_message_sequence"):
            await msgs.add(ctx, **_msg(conv.id, conn, external="b", sequence=1))


async def test_list_after_orders_by_sequence_and_honors_cursor(session_factory):
    async with session_factory() as s:
        ctx = await make_tenant(s)
        conn = await make_connection(s, ctx)
        customer = await CustomerRepository(s).add_customer(ctx, "K")
        conv = await ConversationRepository(s).add(ctx, customer.id, conn)
        msgs = MessageRepository(s)
        for seq in (3, 1, 2):
            await msgs.add(ctx, **_msg(conv.id, conn, external=f"m{seq}", sequence=seq))

        assert [m.sequence for m in await msgs.list_after(ctx, conv.id)] == [1, 2, 3]
        assert [m.sequence for m in await msgs.list_after(ctx, conv.id, after_sequence=1)] == [2, 3]
        assert [m.sequence for m in await msgs.list_after(ctx, conv.id, limit=2)] == [1, 2]


async def test_find_open_ignores_closed_conversation(session_factory):
    from sqlalchemy import text

    async with session_factory() as s:
        ctx = await make_tenant(s)
        conn = await make_connection(s, ctx)
        customer = await CustomerRepository(s).add_customer(ctx, "K")
        convs = ConversationRepository(s)
        conv = await convs.add(ctx, customer.id, conn)
        await s.execute(
            text("UPDATE conversations SET status = 'closed' WHERE id = :id"), {"id": conv.id}
        )
        assert await convs.find_open(ctx, customer.id, conn) is None
