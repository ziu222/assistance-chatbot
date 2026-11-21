"""Bước 2.1: consumer bền cho outbox — debounce, một run mỗi hội thoại, lease có fencing."""

import asyncio
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.modules.agent_runtime.infrastructure.run_requests import (
    DebounceWindow,
    RunRequest,
    claim_next,
    complete,
    enqueue_from_outbox,
    retry_later,
)
from app.modules.conversation.application.receive_inbound_message import ReceiveInboundMessage
from app.modules.conversation.application.staff_conversation import SendStaffMessage
from app.modules.conversation.infrastructure.unit_of_work import SqlConversationUnitOfWork
from app.modules.integration.domain.channel_event import NormalizedChannelEvent
from app.shared.domain.tenant import TenantContext
from app.workers.agent_worker import AgentWorker
from app.workers.outbox_relay import OutboxRelay

from .factories import make_connection, make_tenant

NOW_WINDOW = DebounceWindow(window=timedelta(0), max_wait=timedelta(0))  # chạy ngay
LEASE = timedelta(seconds=120)


class NullPublisher:
    async def publish(self, channel: str, message: str) -> None:
        pass


@pytest.fixture
async def channel(session_factory):
    async with session_factory() as s:
        ctx = await make_tenant(s)
        conn = await make_connection(s, ctx)
        await s.commit()
    return ctx, conn


async def inbound(session_factory, ctx, conn, visitor="v1", text_="xin chào"):
    uow = lambda: SqlConversationUnitOfWork(session_factory)  # noqa: E731
    return await ReceiveInboundMessage(uow)(
        NormalizedChannelEvent(
            tenant_id=ctx.tenant_id,
            channel_connection_id=conn,
            provider="webchat",
            external_message_id=str(uuid4()),
            external_customer_id=visitor,
            direction="inbound",
            message_type="text",
            text=text_,
            occurred_at=datetime.now(UTC),
            trace_id="t",
        )
    )


def relay(session_factory, window: DebounceWindow = NOW_WINDOW) -> OutboxRelay:
    return OutboxRelay(
        session_factory, NullPublisher(), on_published=partial(enqueue_from_outbox, window=window)
    )


async def requests(session_factory) -> list:
    async with session_factory() as s:
        return (
            await s.execute(
                text(
                    "SELECT conversation_id, from_sequence, to_sequence, first_seen_at, due_at, "
                    "lease_until, attempts FROM conversation_run_requests ORDER BY first_seen_at"
                )
            )
        ).all()


async def claim(session_factory, lease: timedelta = LEASE) -> RunRequest | None:
    async with session_factory() as s, s.begin():
        return await claim_next(s, lease)


# ---------- dispatcher (enqueue trong transaction của relay) ----------


async def test_consecutive_messages_coalesce_into_one_request(channel, session_factory):
    ctx, conn = channel
    window = DebounceWindow(window=timedelta(seconds=6), max_wait=timedelta(seconds=20))
    for t in ("alo", "shop ơi", "áo khoác gió size M còn ko"):
        res = await inbound(session_factory, ctx, conn, text_=t)
        await relay(session_factory, window).run_once()

    [row] = await requests(session_factory)
    assert row.conversation_id == res.conversation_id
    assert (row.from_sequence, row.to_sequence) == (1, 3)
    wait = (row.due_at - row.first_seen_at).total_seconds()
    assert 5.9 <= wait <= 20  # gia hạn theo tin cuối, không quá max_wait
    assert row.lease_until is None


async def test_max_wait_caps_the_extension(channel, session_factory):
    ctx, conn = channel
    window = DebounceWindow(window=timedelta(seconds=6), max_wait=timedelta(seconds=20))
    await inbound(session_factory, ctx, conn)
    await relay(session_factory, window).run_once()
    async with session_factory() as s:
        await s.execute(
            text(
                "UPDATE conversation_run_requests SET first_seen_at = now() - interval '19 seconds'"
            )
        )
        await s.commit()

    await inbound(session_factory, ctx, conn, text_="tin 2")
    await relay(session_factory, window).run_once()

    [row] = await requests(session_factory)
    assert (row.due_at - row.first_seen_at).total_seconds() == pytest.approx(20, abs=0.5)


async def test_only_inbound_messages_enqueue(channel, session_factory):
    ctx, conn = channel
    res = await inbound(session_factory, ctx, conn)
    await relay(session_factory).run_once()
    async with session_factory() as s:
        await s.execute(text("DELETE FROM conversation_run_requests"))
        await s.commit()
    staff_user = uuid4()
    await SendStaffMessage(lambda: SqlConversationUnitOfWork(session_factory))(
        TenantContext(ctx.tenant_id),
        res.conversation_id,
        user_id=staff_user,
        client_message_id=str(uuid4()),
        text="chào anh",
        trace_id="t",
    )
    await relay(session_factory).run_once()
    assert await requests(session_factory) == []  # tin của nhân viên không kích hoạt agent


async def test_replayed_old_event_neither_duplicates_nor_rewinds(channel, session_factory):
    ctx, conn = channel
    await inbound(session_factory, ctx, conn)
    await inbound(session_factory, ctx, conn, text_="tin 2")
    await relay(session_factory).run_once()
    async with session_factory() as s:  # replay CHỈ event của tin đầu (runbook: phát lại khoảng ID)
        await s.execute(
            text(
                "UPDATE outbox_events SET published_at = NULL WHERE id = (SELECT min(id) "
                "FROM outbox_events WHERE event_type = 'message.created')"
            )
        )
        await s.commit()
    await relay(session_factory).run_once()

    [row] = await requests(session_factory)
    assert (row.from_sequence, row.to_sequence) == (1, 2)  # không lùi về 1


# ---------- claim / complete / retry ----------


async def test_claim_waits_for_due_and_is_exclusive(channel, session_factory):
    ctx, conn = channel
    window = DebounceWindow(window=timedelta(seconds=30), max_wait=timedelta(seconds=60))
    await inbound(session_factory, ctx, conn)
    await relay(session_factory, window).run_once()
    assert await claim(session_factory) is None  # chưa tới due_at

    async with session_factory() as s:
        await s.execute(text("UPDATE conversation_run_requests SET due_at = now()"))
        await s.commit()
    first = await claim(session_factory)
    assert first is not None and first.attempts == 1
    assert await claim(session_factory) is None  # đang có lease


async def test_complete_deletes_when_nothing_new(channel, session_factory):
    ctx, conn = channel
    await inbound(session_factory, ctx, conn)
    await relay(session_factory).run_once()
    req = await claim(session_factory)

    async with session_factory() as s, s.begin():
        assert await complete(s, req, NOW_WINDOW) == "done"
    assert await requests(session_factory) == []


async def test_new_message_during_run_reschedules_with_fresh_window(channel, session_factory):
    ctx, conn = channel
    window = DebounceWindow(window=timedelta(seconds=6), max_wait=timedelta(seconds=20))
    await inbound(session_factory, ctx, conn)
    await relay(session_factory).run_once()
    req = await claim(session_factory)

    await inbound(session_factory, ctx, conn, text_="còn nữa")  # tới trong lúc agent đang chạy
    await relay(session_factory, window).run_once()
    async with session_factory() as s, s.begin():
        assert await complete(s, req, window) == "rescheduled"

    [row] = await requests(session_factory)
    assert (row.from_sequence, row.to_sequence) == (2, 2)  # chỉ phần chưa xử lý
    assert row.lease_until is None and row.attempts == 0
    assert 5 <= (row.due_at - datetime.now(UTC)).total_seconds() <= 6.5  # cửa sổ gom mới


async def test_expired_lease_is_fenced(channel, session_factory):
    ctx, conn = channel
    await inbound(session_factory, ctx, conn)
    await relay(session_factory).run_once()
    stale = await claim(session_factory)
    async with session_factory() as s:
        await s.execute(
            text("UPDATE conversation_run_requests SET lease_until = now() - interval '1 second'")
        )
        await s.commit()

    fresh = await claim(session_factory)  # worker khác nhận lại sau khi lease hết
    assert fresh is not None and fresh.lease_token != stale.lease_token

    async with session_factory() as s, s.begin():
        assert await complete(s, stale, NOW_WINDOW) == "lost"  # worker cũ không được ghi
    async with session_factory() as s, s.begin():
        assert await complete(s, fresh, NOW_WINDOW) == "done"
    assert await requests(session_factory) == []


async def test_retry_backs_off_then_drops(channel, session_factory):
    ctx, conn = channel
    await inbound(session_factory, ctx, conn)
    await relay(session_factory).run_once()

    req = await claim(session_factory)
    async with session_factory() as s, s.begin():
        assert await retry_later(s, req, timedelta(seconds=30), max_attempts=2) == "rescheduled"
    [row] = await requests(session_factory)
    assert row.lease_until is None and (row.due_at - datetime.now(UTC)).total_seconds() > 25

    async with session_factory() as s:
        await s.execute(text("UPDATE conversation_run_requests SET due_at = now()"))
        await s.commit()
    req = await claim(session_factory)
    assert req.attempts == 2
    async with session_factory() as s, s.begin():
        assert await retry_later(s, req, timedelta(seconds=30), max_attempts=2) == "dropped"
    assert await requests(session_factory) == []


# ---------- worker ----------


class RecordingHandler:
    def __init__(self, delay: float = 0.0, fail_times: int = 0):
        self.calls: list[tuple] = []
        self.delay = delay
        self.fail_times = fail_times

    async def __call__(self, req: RunRequest) -> None:
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("model timeout")
        await asyncio.sleep(self.delay)
        self.calls.append((req.conversation_id, req.from_sequence, req.to_sequence))


def worker(session_factory, handler, **kw) -> AgentWorker:
    defaults = {"window": NOW_WINDOW, "lease": LEASE, "max_attempts": 3, "retry_base": 0.0}
    return AgentWorker(session_factory, handler, **(defaults | kw))


async def test_concurrent_workers_run_each_conversation_once(channel, session_factory):
    ctx, conn = channel
    convs = set()
    for i in range(12):
        convs.add((await inbound(session_factory, ctx, conn, visitor=f"v{i}")).conversation_id)
    await relay(session_factory).run_once()

    handler = RecordingHandler(delay=0.01)
    workers = [worker(session_factory, handler) for _ in range(3)]
    while await requests(session_factory):
        await asyncio.gather(*(w.process_once() for w in workers))

    assert sorted(c for c, _, _ in handler.calls) == sorted(convs)  # đủ, không trùng


async def test_worker_retries_transient_failure(channel, session_factory):
    ctx, conn = channel
    await inbound(session_factory, ctx, conn)
    await relay(session_factory).run_once()
    handler = RecordingHandler(fail_times=1)
    w = worker(session_factory, handler)

    assert await w.process_once() is True  # lần 1 lỗi -> hẹn lại
    assert handler.calls == []
    assert await w.process_once() is True  # retry_base=0 => tới hạn ngay
    assert len(handler.calls) == 1
    assert await requests(session_factory) == []


async def test_run_loop_end_to_end_and_stops(channel, session_factory):
    ctx, conn = channel
    res = await inbound(session_factory, ctx, conn)
    await relay(session_factory).run_once()
    handler = RecordingHandler()
    stop = asyncio.Event()
    task = asyncio.create_task(
        worker(session_factory, handler, idle_sleep=0.01).run(stop, concurrency=2)
    )
    for _ in range(100):
        if handler.calls:
            break
        await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert handler.calls == [(res.conversation_id, 1, 1)]
