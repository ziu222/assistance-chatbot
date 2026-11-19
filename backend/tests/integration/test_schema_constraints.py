"""Các bất biến phải do DB đảm bảo (không dựa vào code ứng dụng)."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .factories import (
    insert_message,
    make_connection,
    make_conversation,
    make_customer,
    make_tenant,
)


async def _two_tenants(s):
    a, b = await make_tenant(s), await make_tenant(s)
    return a, b


async def test_identity_cannot_reference_other_tenants_customer(session_factory):
    async with session_factory() as s:
        a, b = await _two_tenants(s)
        conn_b = await make_connection(s, b)
        customer_a = await make_customer(s, a)
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(IntegrityError, match="foreign key"):
            await s.execute(
                text(
                    "INSERT INTO channel_identities (id, tenant_id, customer_id, "
                    "channel_connection_id, provider, scoped_external_id) "
                    "VALUES (:id, :t, :cu, :co, 'webchat', 'v1')"
                ),
                {"id": uuid4(), "t": b.tenant_id, "cu": customer_a, "co": conn_b},
            )


async def test_conversation_cannot_reference_other_tenants_customer(session_factory):
    async with session_factory() as s:
        a, b = await _two_tenants(s)
        conn_b = await make_connection(s, b)
        customer_a = await make_customer(s, a)
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(IntegrityError, match="foreign key"):
            await make_conversation(s, b, customer_a, conn_b)


async def test_conversation_cannot_use_other_tenants_connection(session_factory):
    async with session_factory() as s:
        a, b = await _two_tenants(s)
        conn_a = await make_connection(s, a)
        customer_b = await make_customer(s, b)
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(IntegrityError, match="foreign key"):
            await make_conversation(s, b, customer_b, conn_a)


async def test_message_cannot_reference_other_tenants_conversation(session_factory):
    async with session_factory() as s:
        a, b = await _two_tenants(s)
        conn_a, conn_b = await make_connection(s, a), await make_connection(s, b)
        conv_a = await make_conversation(s, a, await make_customer(s, a), conn_a)
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(IntegrityError, match="foreign key"):
            await insert_message(s, b, conv_a, conn_b)


async def _conversation(s):
    ctx = await make_tenant(s)
    conn = await make_connection(s, ctx)
    customer = await make_customer(s, ctx)
    conv = await make_conversation(s, ctx, customer, conn)
    return ctx, conn, customer, conv


async def test_message_idempotency_key_is_unique(session_factory):
    async with session_factory() as s:
        ctx, conn, _, conv = await _conversation(s)
        await insert_message(s, ctx, conv, conn, external_id="m1", sequence=1)
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(IntegrityError, match="uq_message_idempotency"):
            await insert_message(s, ctx, conv, conn, external_id="m1", sequence=2)


async def test_same_external_id_allowed_across_directions(session_factory):
    async with session_factory() as s:
        ctx, conn, _, conv = await _conversation(s)
        await insert_message(s, ctx, conv, conn, external_id="m1", sequence=1)
        await insert_message(s, ctx, conv, conn, external_id="m1", sequence=2, direction="outbound")
        await s.commit()


async def test_message_sequence_is_unique_per_conversation(session_factory):
    async with session_factory() as s:
        ctx, conn, _, conv = await _conversation(s)
        await insert_message(s, ctx, conv, conn, sequence=1)
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(IntegrityError, match="uq_message_sequence"):
            await insert_message(s, ctx, conv, conn, sequence=1)


@pytest.mark.parametrize("content", ["", "x" * 4001])
async def test_message_content_length_is_checked(session_factory, content):
    async with session_factory() as s:
        ctx, conn, _, conv = await _conversation(s)
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(IntegrityError, match="check"):
            await insert_message(s, ctx, conv, conn, content=content)


async def test_only_one_open_conversation_per_customer_and_connection(session_factory):
    async with session_factory() as s:
        ctx, conn, customer, _ = await _conversation(s)
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(IntegrityError, match="uq_conversation_one_open"):
            await make_conversation(s, ctx, customer, conn)


async def test_closed_conversation_does_not_block_a_new_open_one(session_factory):
    async with session_factory() as s:
        ctx, conn, customer, conv = await _conversation(s)
        await s.execute(
            text("UPDATE conversations SET status = 'closed' WHERE id = :id"), {"id": conv}
        )
        await make_conversation(s, ctx, customer, conn)
        await s.commit()


async def test_channel_identity_is_unique_per_connection(session_factory):
    async with session_factory() as s:
        ctx, conn, customer, _ = await _conversation(s)
        insert = text(
            "INSERT INTO channel_identities (id, tenant_id, customer_id, channel_connection_id, "
            "provider, scoped_external_id) VALUES (:id, :t, :cu, :co, 'webchat', 'v1')"
        )
        params = {"t": ctx.tenant_id, "cu": customer, "co": conn}
        await s.execute(insert, {"id": uuid4(), **params})
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(IntegrityError, match="uq_channel_identity"):
            await s.execute(insert, {"id": uuid4(), **params})
