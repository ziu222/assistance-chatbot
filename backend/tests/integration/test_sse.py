"""SSE hub (§7.3, §8.5): đúng kênh, heartbeat, resync khi đầy/mất Redis, giới hạn kết nối."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
import uvicorn
from sqlalchemy import text

from app.config.settings import Settings
from app.main import create_app
from app.modules.conversation.application.receive_inbound_message import ReceiveInboundMessage
from app.modules.conversation.application.staff_conversation import SendStaffMessage
from app.modules.conversation.infrastructure.unit_of_work import SqlConversationUnitOfWork
from app.modules.integration.domain.channel_event import NormalizedChannelEvent
from app.shared.domain.tenant import TenantContext
from app.shared.infrastructure.sse_hub import (
    OVERFLOW,
    RESYNC,
    SignalHub,
    TooManyStreams,
    sse_frames,
)
from app.workers.outbox_relay import OutboxRelay, RedisPublisher

from .conftest import TEST_REDIS_URL, TEST_SETTINGS
from .test_staff_api import CSRF, PASSWORD, staff

# ---------- hub với Redis thật ----------


@pytest_asyncio.fixture
async def hub():
    h = SignalHub(TEST_REDIS_URL, queue_size=3)
    h.start()
    await asyncio.wait_for(h.connected.wait(), 3)
    yield h
    await h.stop()


@pytest_asyncio.fixture
async def redis_client():
    c = aioredis.from_url(TEST_REDIS_URL)
    yield c
    await c.aclose()


async def get(sub, timeout=2.0):
    return await asyncio.wait_for(sub.queue.get(), timeout)


async def test_delivers_only_to_subscribed_channel(hub, redis_client):
    a = hub.open("evt:t:A", limit_key="t:A", limit=5)
    b = hub.open("evt:t:B", limit_key="t:B", limit=5)
    await redis_client.publish("evt:t:A", '{"id":1}')
    assert await get(a) == b'{"id":1}'
    await asyncio.sleep(0.2)
    assert b.queue.empty()  # tenant B không nhận tín hiệu của tenant A


async def test_limit_and_slot_release(hub):
    subs = [hub.open("evt:v:c:v", limit_key="v:c:v", limit=3) for _ in range(3)]
    with pytest.raises(TooManyStreams):
        hub.open("evt:v:c:v", limit_key="v:c:v", limit=3)
    hub.close(subs[0])
    hub.close(subs[0])  # idempotent
    hub.open("evt:v:c:v", limit_key="v:c:v", limit=3)


async def test_full_queue_is_replaced_by_overflow(hub, redis_client):
    sub = hub.open("evt:t:X", limit_key="t:X", limit=5)
    for i in range(10):
        await redis_client.publish("evt:t:X", json.dumps({"id": i}))
    await asyncio.sleep(0.3)
    assert sub.queue.qsize() <= 3
    items = [sub.queue.get_nowait() for _ in range(sub.queue.qsize())]
    assert OVERFLOW in items


async def test_resync_after_redis_connection_drop(hub, redis_client):
    sub = hub.open("evt:t:R", limit_key="t:R", limit=5)
    await hub._pubsub.connection.disconnect()  # mô phỏng Redis rớt kết nối
    assert await get(sub, timeout=5) is RESYNC  # có thể đã lỡ tín hiệu -> client refetch
    await asyncio.wait_for(hub.connected.wait(), 5)
    await redis_client.publish("evt:t:R", '{"id":9}')
    assert await get(sub) == b'{"id":9}'


# ---------- định dạng frame ----------


class FakeHub:
    def __init__(self, items):
        self.items = items
        self.closed = False

    def close(self, sub):
        self.closed = True


class Sub:
    def __init__(self, items):
        self.queue = asyncio.Queue()
        for i in items:
            self.queue.put_nowait(i)


async def collect(gen, n=None, timeout=1.0):
    out = []
    try:
        async with asyncio.timeout(timeout):
            async for frame in gen:
                out.append(frame)
                if n and len(out) == n:
                    break
    except TimeoutError:
        pass
    return out


async def test_frames_signal_resync_overflow():
    sub = Sub(
        [b'{"id":7,"t":"message.created","c":"C","s":3}', b"not json", RESYNC, OVERFLOW, b"{}"]
    )
    hub = FakeHub([])
    frames = await collect(sse_frames(hub, sub, heartbeat=10, max_duration=10))
    assert frames == [
        "retry: 3000\n\n",
        'event: signal\nid: 7\ndata: {"t":"message.created","c":"C","s":3}\n\n',
        "event: resync\ndata: {}\n\n",
        "event: resync\ndata: {}\n\n",  # OVERFLOW: resync rồi đóng
    ]
    assert hub.closed


async def test_heartbeat_and_max_duration():
    hub = FakeHub([])
    frames = await collect(sse_frames(hub, Sub([]), heartbeat=0.05, max_duration=0.18))
    assert frames[0] == "retry: 3000\n\n"
    assert 2 <= frames.count(": heartbeat\n\n") <= 4
    assert hub.closed  # hết thời lượng tối đa -> đóng để client xác thực lại


# ---------- end-to-end qua uvicorn thật ----------


@pytest_asyncio.fixture
async def live(migrated_db_url):
    r = aioredis.from_url(TEST_REDIS_URL)
    await r.flushdb()
    await r.aclose()
    app = create_app(Settings(**({"database_url": migrated_db_url} | TEST_SETTINGS)))
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="warning", timeout_graceful_shutdown=1
        )
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    await asyncio.wait_for(app.state.signal_hub.connected.wait(), 3)
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
        yield client, app
    server.should_exit = True
    await task


@asynccontextmanager
async def events(client, path, headers):
    async with client.stream("GET", path, headers=headers) as r:
        yield r, r.aiter_lines()


async def next_signal(lines, timeout=3.0) -> dict | None:
    """Đọc tới frame `event: signal`, trả data; None nếu hết thời gian."""
    event = None
    try:
        async with asyncio.timeout(timeout):
            async for line in lines:
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: ") and event == "signal":
                    return json.loads(line[6:])
    except TimeoutError:
        return None
    return None


async def staff_cookie(client, session_factory, **kw):
    ctx, uid, email, conn_id = await staff(session_factory, **kw)
    r = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}, headers=CSRF
    )
    assert r.status_code == 200
    # Cookie Secure không được httpx gửi qua http://; test gắn tay.
    return {"Cookie": f"__Host-sid={r.cookies['__Host-sid']}"}, ctx, uid, conn_id


async def relay_once(session_factory):
    client = aioredis.from_url(TEST_REDIS_URL)
    try:
        await OutboxRelay(session_factory, RedisPublisher(client)).run_once()
    finally:
        await client.aclose()


async def visitor_token(client, public_key):
    r = await client.post(
        "/public/webchat/v1/sessions",
        json={"public_key": public_key},
        headers={"Origin": TEST_SETTINGS["public_origin"]},
    )
    return r.json()["token"]


async def public_key_of(session_factory, conn_id):
    async with session_factory() as s:
        return (
            await s.execute(
                text("SELECT public_key FROM channel_connections WHERE id = :id"), {"id": conn_id}
            )
        ).scalar_one()


async def test_staff_stream_receives_own_tenant_signals_only(live, session_factory):
    client, _ = live
    headers_a, ctx_a, _, conn_a = await staff_cookie(client, session_factory)
    headers_b, *_ = await staff_cookie(client, session_factory)

    async with (
        events(client, "/api/v1/events", headers_a) as (ra, lines_a),
        events(client, "/api/v1/events", headers_b) as (rb, lines_b),
    ):
        assert ra.status_code == rb.status_code == 200
        assert ra.headers["content-type"].startswith("text/event-stream")
        assert ra.headers["cache-control"] == "no-cache"
        assert ra.headers["x-accel-buffering"] == "no"

        uow = lambda: SqlConversationUnitOfWork(session_factory)  # noqa: E731
        res = await ReceiveInboundMessage(uow)(
            NormalizedChannelEvent(
                tenant_id=ctx_a.tenant_id,
                channel_connection_id=conn_a,
                provider="webchat",
                external_message_id=str(uuid4()),
                external_customer_id="v1",
                direction="inbound",
                message_type="text",
                text="xin chào",
                occurred_at=datetime.now(UTC),
                trace_id="t",
            )
        )
        await relay_once(session_factory)

        got = []
        while (sig := await next_signal(lines_a, timeout=1.5)) is not None:
            got.append(sig)
            if any(g["t"] == "message.created" for g in got):
                break
        assert {"t": "message.created", "c": str(res.conversation_id), "s": 1} in got
        assert await next_signal(lines_b, timeout=0.5) is None  # tenant B im lặng


async def test_visitor_stream_receives_staff_reply(live, session_factory):
    client, _ = live
    _, ctx, uid, conn_id = await staff_cookie(client, session_factory)
    token = await visitor_token(client, await public_key_of(session_factory, conn_id))
    bearer = {"Authorization": f"Bearer {token}"}

    async with events(client, "/public/webchat/v1/events", bearer) as (r, lines):
        assert r.status_code == 200
        sent = await client.post(
            "/public/webchat/v1/messages",
            json={"client_message_id": str(uuid4()), "text": "cần hỗ trợ"},
            headers=bearer,
        )
        conv = sent.json()["conversation_id"]
        await SendStaffMessage(lambda: SqlConversationUnitOfWork(session_factory))(
            TenantContext(ctx.tenant_id),
            UUID(conv),
            user_id=uid,
            client_message_id=str(uuid4()),
            text="chào anh",
            trace_id="t",
        )
        await relay_once(session_factory)

        seen = []
        while (sig := await next_signal(lines, timeout=1.5)) is not None:
            seen.append(sig)
            if sig.get("s") == 2:
                break
        assert {"t": "message.created", "c": conv, "s": 2} in seen


async def test_streams_require_auth(live):
    client, _ = live
    assert (await client.get("/api/v1/events")).status_code == 401
    r = await client.get("/public/webchat/v1/events", headers={"Authorization": "Bearer x"})
    assert r.status_code == 401


async def test_visitor_stream_limit_and_release_on_disconnect(live, session_factory):
    client, app = live
    _, _, _, conn_id = await staff_cookie(client, session_factory)
    bearer = {
        "Authorization": "Bearer "
        + await visitor_token(client, await public_key_of(session_factory, conn_id))
    }

    async def open_stream():
        req = client.build_request("GET", "/public/webchat/v1/events", headers=bearer)
        return await client.send(req, stream=True)

    streams = [await open_stream() for _ in range(3)]
    assert [s.status_code for s in streams] == [200] * 3
    fourth = await open_stream()
    assert fourth.status_code == 429
    await fourth.aclose()

    await streams[0].aclose()  # khách đóng tab -> slot phải được trả
    for _ in range(50):
        if sum(app.state.signal_hub.counts.values()) == 2:
            break
        await asyncio.sleep(0.05)
    again = await open_stream()
    assert again.status_code == 200
    for s in [*streams[1:], again]:
        await s.aclose()
