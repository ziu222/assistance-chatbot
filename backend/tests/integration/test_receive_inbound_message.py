"""Bất biến của bước 1.3 dưới đồng thời: idempotency, sequence, race khởi tạo, nguyên tử."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.conversation.application.receive_inbound_message import ReceiveInboundMessage
from app.modules.conversation.infrastructure.unit_of_work import SqlConversationUnitOfWork
from app.modules.integration.domain.channel_event import NormalizedChannelEvent

from .factories import make_connection, make_tenant


@pytest.fixture
def receive(session_factory):
    return ReceiveInboundMessage(lambda: SqlConversationUnitOfWork(session_factory))


@pytest.fixture
async def channel(session_factory):
    async with session_factory() as s:
        ctx = await make_tenant(s)
        conn = await make_connection(s, ctx)
        await s.commit()
    return ctx, conn


def event(ctx, conn, *, visitor="v1", external=None, text_="xin chào"):
    return NormalizedChannelEvent(
        tenant_id=ctx.tenant_id,
        channel_connection_id=conn,
        provider="webchat",
        external_message_id=external or str(uuid4()),
        external_customer_id=visitor,
        direction="inbound",
        message_type="text",
        text=text_,
        occurred_at=datetime.now(UTC),
        trace_id="trace-test",
    )


async def scalar(session_factory, sql, **params):
    async with session_factory() as s:
        return (await s.execute(text(sql), params)).scalar_one()


async def test_first_message_creates_customer_identity_conversation(
    receive, channel, session_factory
):
    ctx, conn = channel
    result = await receive(event(ctx, conn, visitor="0123abcd-visitor"))

    assert result.created is True
    assert result.sequence == 1
    assert await scalar(session_factory, "SELECT count(*) FROM customers") == 1
    assert await scalar(session_factory, "SELECT count(*) FROM channel_identities") == 1
    name = await scalar(session_factory, "SELECT display_name FROM customers")
    assert name == "Khách #0123"
    async with session_factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT last_sequence, unread_count, last_message_preview, version "
                    "FROM conversations WHERE id = :id"
                ),
                {"id": result.conversation_id},
            )
        ).one()
    assert tuple(row) == (1, 1, "xin chào", 2)
    last_inbound = await scalar(session_factory, "SELECT last_inbound_at FROM channel_identities")
    assert last_inbound is not None


async def test_outbox_events_written_with_ids_only(receive, channel, session_factory):
    ctx, conn = channel
    result = await receive(event(ctx, conn, visitor="v-out", text_="bí mật"))

    async with session_factory() as s:
        rows = (
            await s.execute(text("SELECT event_type, payload FROM outbox_events ORDER BY id"))
        ).all()
    assert [r.event_type for r in rows] == ["conversation.created", "message.created"]
    payload = rows[1].payload
    assert payload["v"] == 1
    assert payload["message_id"] == str(result.message_id)
    assert payload["conversation_id"] == str(result.conversation_id)
    assert payload["sequence"] == 1
    assert payload["visitor_id"] == "v-out"
    assert payload["trace_id"] == "trace-test"
    assert "bí mật" not in str(rows)  # không mang nội dung tin


async def test_duplicate_returns_original_without_new_rows(receive, channel, session_factory):
    ctx, conn = channel
    ev = event(ctx, conn)
    first = await receive(ev)
    second = await receive(ev)

    assert second.created is False
    assert (second.message_id, second.sequence) == (first.message_id, first.sequence)
    assert await scalar(session_factory, "SELECT count(*) FROM messages") == 1
    assert (
        await scalar(
            session_factory,
            "SELECT count(*) FROM outbox_events WHERE event_type = 'message.created'",
        )
        == 1
    )


async def test_concurrent_duplicates_store_exactly_one(receive, channel, session_factory):
    ctx, conn = channel
    ev = event(ctx, conn, visitor="v-dup")

    results = await asyncio.gather(*(receive(ev) for _ in range(50)))

    assert len({r.message_id for r in results}) == 1
    assert sum(r.created for r in results) == 1
    assert await scalar(session_factory, "SELECT count(*) FROM messages") == 1
    assert (
        await scalar(
            session_factory,
            "SELECT count(*) FROM outbox_events WHERE event_type = 'message.created'",
        )
        == 1
    )


async def test_concurrent_messages_get_gapless_sequence(receive, channel, session_factory):
    ctx, conn = channel
    n = 100

    results = await asyncio.gather(*(receive(event(ctx, conn, visitor="v-seq")) for _ in range(n)))

    assert sorted(r.sequence for r in results) == list(range(1, n + 1))
    assert len({r.conversation_id for r in results}) == 1
    async with session_factory() as s:
        row = (await s.execute(text("SELECT last_sequence, unread_count FROM conversations"))).one()
    assert tuple(row) == (n, n)
    assert (
        await scalar(
            session_factory,
            "SELECT count(*) FROM outbox_events WHERE event_type = 'message.created'",
        )
        == n
    )


async def test_concurrent_first_messages_create_one_customer_and_conversation(
    receive, channel, session_factory
):
    ctx, conn = channel
    visitors = [f"v-race-{i}" for i in range(10)]

    # mỗi khách mới gửi 2 tin đầu cùng lúc
    await asyncio.gather(
        *(receive(event(ctx, conn, visitor=v)) for v in visitors for _ in range(2))
    )

    assert await scalar(session_factory, "SELECT count(*) FROM customers") == 10
    assert await scalar(session_factory, "SELECT count(*) FROM channel_identities") == 10
    assert await scalar(session_factory, "SELECT count(*) FROM conversations") == 10
    assert (
        await scalar(
            session_factory,
            "SELECT count(*) FROM outbox_events WHERE event_type = 'conversation.created'",
        )
        == 10
    )


async def test_failure_before_commit_leaves_nothing_behind(channel, session_factory):
    ctx, conn = channel

    class FailingOutboxUow(SqlConversationUnitOfWork):
        async def __aenter__(self):
            uow = await super().__aenter__()
            original = uow.outbox.add

            async def boom(*args, **kwargs):
                if kwargs.get("event_type") == "message.created":
                    raise RuntimeError("injected failure")
                return await original(*args, **kwargs)

            uow.outbox.add = boom
            return uow

    receive = ReceiveInboundMessage(lambda: FailingOutboxUow(session_factory))
    with pytest.raises(RuntimeError, match="injected"):
        await receive(event(ctx, conn))

    for table in ("customers", "channel_identities", "conversations", "messages", "outbox_events"):
        assert await scalar(session_factory, f"SELECT count(*) FROM {table}") == 0, table


async def test_message_after_close_opens_new_conversation(receive, channel, session_factory):
    ctx, conn = channel
    first = await receive(event(ctx, conn, visitor="v-close"))
    async with session_factory() as s:
        await s.execute(
            text("UPDATE conversations SET status = 'closed', closed_at = now() WHERE id = :id"),
            {"id": first.conversation_id},
        )
        await s.commit()

    second = await receive(event(ctx, conn, visitor="v-close"))

    assert second.conversation_id != first.conversation_id
    assert second.sequence == 1
    assert await scalar(session_factory, "SELECT count(*) FROM customers") == 1


async def test_same_visitor_id_on_other_connection_is_other_customer(
    receive, channel, session_factory
):
    ctx, conn = channel
    async with session_factory() as s:
        conn2 = await make_connection(s, ctx)
        await s.commit()

    a = await receive(event(ctx, conn, visitor="same"))
    b = await receive(event(ctx, conn2, visitor="same"))

    assert a.conversation_id != b.conversation_id
    assert await scalar(session_factory, "SELECT count(*) FROM customers") == 2


async def test_connection_of_other_tenant_is_rejected(receive, channel, session_factory):
    _, conn_a = channel
    async with session_factory() as s:
        ctx_b = await make_tenant(s)
        await s.commit()

    with pytest.raises(IntegrityError):
        await receive(event(ctx_b, conn_a))

    for table in ("customers", "conversations", "messages", "outbox_events"):
        assert await scalar(session_factory, f"SELECT count(*) FROM {table}") == 0, table
