"""Relay: at-least-once, không mất event khi crash/Redis lỗi, SKIP LOCKED không publish trùng."""

import asyncio
import json
import os
from datetime import timedelta
from uuid import uuid4

import pytest
import redis.asyncio as aioredis
from sqlalchemy import text

from app.shared.domain.tenant import TenantContext
from app.shared.infrastructure.outbox import OutboxWriter
from app.workers.outbox_relay import OutboxRelay, RedisPublisher, outbox_backlog, purge_published

from .factories import make_tenant

TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


class RecordingPublisher:
    def __init__(self, fail_after: int | None = None, exc: BaseException | None = None):
        self.sent: list[tuple[str, dict]] = []
        self.fail_after = fail_after
        self.exc = exc
        self.calls = 0

    async def publish(self, channel: str, message: str) -> None:
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise self.exc
        self.calls += 1
        self.sent.append((channel, json.loads(message)))

    def ids(self) -> list[int]:
        return [m["id"] for c, m in self.sent if c.startswith("evt:t:")]


class Killed(BaseException):
    """Mô phỏng process chết giữa batch (không phải Exception -> relay không bắt)."""


async def seed(
    session_factory, n: int, *, visitor: str | None = "v1"
) -> tuple[TenantContext, list]:
    async with session_factory() as s:
        ctx = await make_tenant(s)
        writer = OutboxWriter(s)
        for i in range(n):
            payload = {
                "tenant_id": str(ctx.tenant_id),
                "connection_id": str(uuid4()),
                "conversation_id": str(uuid4()),
                "sequence": i + 1,
            }
            if visitor:
                payload["visitor_id"] = visitor
            await writer.add(
                ctx,
                aggregate_type="conversation",
                aggregate_id=uuid4(),
                event_type="message.created",
                payload=payload,
                trace_id=None,
            )
        await s.commit()
        ids = (await s.execute(text("SELECT id FROM outbox_events ORDER BY id"))).scalars().all()
    return ctx, list(ids)


async def unpublished(session_factory) -> int:
    async with session_factory() as s:
        return (
            await s.execute(text("SELECT count(*) FROM outbox_events WHERE published_at IS NULL"))
        ).scalar_one()


async def test_publishes_in_id_order_and_marks_published(session_factory):
    _, ids = await seed(session_factory, 5)
    pub = RecordingPublisher()

    published = await OutboxRelay(session_factory, pub).run_once()

    assert published == 5
    assert pub.ids() == ids
    assert len(pub.sent) == 10  # tenant + visitor channel cho mỗi event
    assert await unpublished(session_factory) == 0
    assert await OutboxRelay(session_factory, pub).run_once() == 0  # không publish lại


async def test_batch_size_is_respected(session_factory):
    await seed(session_factory, 5)
    pub = RecordingPublisher()

    assert await OutboxRelay(session_factory, pub, batch_size=2).run_once() == 2
    assert await unpublished(session_factory) == 3


async def test_crash_mid_batch_loses_nothing(session_factory):
    _, ids = await seed(session_factory, 10, visitor=None)

    crashing = RecordingPublisher(fail_after=4, exc=Killed())
    with pytest.raises(Killed):
        await OutboxRelay(session_factory, crashing).run_once()
    assert (
        await unpublished(session_factory) == 10
    )  # transaction rollback, chưa event nào bị đánh dấu

    restarted = RecordingPublisher()
    assert await OutboxRelay(session_factory, restarted).run_once() == 10
    assert set(crashing.ids()) | set(restarted.ids()) == set(ids)
    assert restarted.ids() == ids  # at-least-once: 4 event đầu được gửi lại


async def test_publish_error_records_attempt_and_keeps_event(session_factory):
    _, ids = await seed(session_factory, 3, visitor=None)
    failing = RecordingPublisher(fail_after=1, exc=ConnectionError("redis down"))

    published = await OutboxRelay(session_factory, failing).run_once()

    assert published == 1
    async with session_factory() as s:
        rows = (
            await s.execute(
                text("SELECT id, published_at, attempts, last_error FROM outbox_events ORDER BY id")
            )
        ).all()
    assert rows[0].published_at is not None
    assert (rows[1].attempts, rows[1].last_error) == (1, "ConnectionError: redis down")
    assert rows[2].published_at is None and rows[2].attempts == 0  # dừng batch ở lỗi đầu

    assert await OutboxRelay(session_factory, RecordingPublisher()).run_once() == 2
    assert await unpublished(session_factory) == 0


async def test_concurrent_relays_do_not_double_publish(session_factory):
    _, ids = await seed(session_factory, 40, visitor=None)

    class SlowPublisher(RecordingPublisher):
        async def publish(self, channel, message):
            await asyncio.sleep(0.005)
            await super().publish(channel, message)

    pubs = [SlowPublisher(), SlowPublisher()]
    relays = [OutboxRelay(session_factory, p, batch_size=10) for p in pubs]

    while await unpublished(session_factory):
        await asyncio.gather(*(r.run_once() for r in relays))

    all_ids = pubs[0].ids() + pubs[1].ids()
    assert sorted(all_ids) == ids  # đủ và không trùng
    assert pubs[0].ids() and pubs[1].ids()  # cả hai thực sự cùng làm


async def test_run_loop_backs_off_on_errors_and_stops(session_factory):
    await seed(session_factory, 2, visitor=None)
    pub = RecordingPublisher(fail_after=0, exc=ConnectionError("down"))
    relay = OutboxRelay(session_factory, pub, idle_sleep=0.01, max_backoff=0.05)
    stop = asyncio.Event()

    task = asyncio.create_task(relay.run(stop))
    await asyncio.sleep(0.2)
    pub.fail_after = None  # Redis hồi phục
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=2)

    assert await unpublished(session_factory) == 0
    assert relay.stats.failed >= 1
    assert relay.stats.published == 2


async def test_backlog_reports_count_and_oldest_age(session_factory):
    async with session_factory() as s:
        count, age = await outbox_backlog(s)
    assert (count, age) == (0, 0.0)

    await seed(session_factory, 3)
    async with session_factory() as s:
        await s.execute(text("UPDATE outbox_events SET created_at = now() - interval '30 seconds'"))
        await s.commit()
        count, age = await outbox_backlog(s)
    assert count == 3
    assert 29 <= age < 120


async def test_purge_removes_only_old_published(session_factory):
    await seed(session_factory, 3, visitor=None)
    await OutboxRelay(session_factory, RecordingPublisher(), batch_size=2).run_once()
    async with session_factory() as s:
        await s.execute(
            text(
                "UPDATE outbox_events SET published_at = now() - interval '8 days' "
                "WHERE published_at IS NOT NULL"
            )
        )
        await s.commit()
        removed = await purge_published(s, older_than=timedelta(days=7))
        await s.commit()
        left = (await s.execute(text("SELECT count(*) FROM outbox_events"))).scalar_one()
    assert removed == 2
    assert left == 1  # event chưa publish không bao giờ bị xóa


async def test_end_to_end_with_real_redis(session_factory):
    ctx, ids = await seed(session_factory, 1, visitor="v-redis")
    client = aioredis.from_url(TEST_REDIS_URL)
    pubsub = client.pubsub()
    await pubsub.subscribe(f"evt:t:{ctx.tenant_id}")
    await pubsub.get_message(timeout=1)  # xác nhận subscribe

    try:
        published = await OutboxRelay(session_factory, RedisPublisher(client)).run_once()
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    finally:
        await pubsub.aclose()
        await client.aclose()

    assert published == 1
    assert msg is not None
    signal = json.loads(msg["data"])
    assert signal["id"] == ids[0]
    assert signal["t"] == "message.created"
