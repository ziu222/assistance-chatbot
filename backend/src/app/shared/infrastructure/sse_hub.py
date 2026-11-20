"""SSE hub trong API process (thiết kế §8.5): MỘT subscriber Redis dùng chung, phát tín hiệu
tới hàng đợi có biên của từng kết nối SSE.

SSE chỉ là tín hiệu; REST là sự thật. Vì vậy khi có thể đã lỡ tín hiệu (mất Redis, client chậm)
hub gửi `resync` để client refetch, thay vì cố đảm bảo giao đủ.
"""

import asyncio
import contextlib
import json
import logging
from collections import Counter, defaultdict
from collections.abc import AsyncIterator

import redis.asyncio as aioredis

from app.shared.infrastructure.metrics import SSE_CONNECTIONS, SSE_RESYNC

log = logging.getLogger(__name__)

RESYNC = object()  # có thể đã lỡ tín hiệu: client refetch, giữ kết nối
OVERFLOW = object()  # client đọc chậm, hàng đợi đầy: gửi resync rồi đóng


class TooManyStreams(Exception):
    pass


class Subscription:
    def __init__(self, channel: str, limit_key: str, queue_size: int) -> None:
        self.channel = channel
        self.limit_key = limit_key
        self.queue: asyncio.Queue = asyncio.Queue(queue_size)
        self.closed = False
        self.audience = "staff" if channel.startswith("evt:t:") else "visitor"


class SignalHub:
    def __init__(self, redis_url: str, *, queue_size: int = 100) -> None:
        # Kết nối riêng, không socket_timeout: listen() chờ vô hạn; health check phát hiện chết.
        self._redis = aioredis.from_url(
            redis_url, socket_connect_timeout=2, health_check_interval=15
        )
        self._queue_size = queue_size
        self._subs: dict[str, set[Subscription]] = defaultdict(set)
        # ponytail: đếm theo process; N instance => giới hạn thực tế x N. Đếm qua Redis khi cần.
        self.counts: Counter[str] = Counter()
        self.connected = asyncio.Event()
        self._pubsub = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._redis.aclose()

    def open(self, channel: str, *, limit_key: str, limit: int) -> Subscription:
        if self.counts[limit_key] >= limit:
            raise TooManyStreams
        sub = Subscription(channel, limit_key, self._queue_size)
        self.counts[limit_key] += 1
        SSE_CONNECTIONS.labels(audience=sub.audience).inc()
        self._subs[channel].add(sub)
        return sub

    def close(self, sub: Subscription) -> None:
        if sub.closed:
            return
        sub.closed = True
        self._subs[sub.channel].discard(sub)
        if not self._subs[sub.channel]:
            del self._subs[sub.channel]
        self.counts[sub.limit_key] -= 1
        SSE_CONNECTIONS.labels(audience=sub.audience).dec()
        if self.counts[sub.limit_key] <= 0:
            del self.counts[sub.limit_key]

    def _put(self, sub: Subscription, item: object) -> None:
        try:
            sub.queue.put_nowait(item)
        except asyncio.QueueFull:
            # Không để một client chậm làm nghẽn hub: bỏ phần tồn, chỉ còn lệnh đóng + resync.
            while not sub.queue.empty():
                sub.queue.get_nowait()
            sub.queue.put_nowait(OVERFLOW)
            SSE_RESYNC.inc()

    def _broadcast_resync(self) -> None:
        for subs in self._subs.values():
            for sub in subs:
                self._put(sub, RESYNC)
                SSE_RESYNC.inc()

    async def _run(self) -> None:
        backoff, had_failure = 0.2, False
        while True:
            self._pubsub = self._redis.pubsub()
            try:
                await self._pubsub.psubscribe("evt:*")
                self.connected.set()
                if had_failure:
                    self._broadcast_resync()
                backoff, had_failure = 0.2, False
                async for msg in self._pubsub.listen():
                    if msg["type"] != "pmessage":
                        continue
                    channel = msg["channel"].decode()
                    for sub in list(self._subs.get(channel, ())):
                        self._put(sub, msg["data"])
                raise ConnectionError("pubsub stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected.clear()
                had_failure = True
                log.warning("sse hub lost redis", extra={"error_type": type(exc).__name__})
            finally:
                with contextlib.suppress(Exception):
                    await self._pubsub.aclose()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)


RESYNC_FRAME = "event: resync\ndata: {}\n\n"


async def sse_frames(
    hub: SignalHub, sub: Subscription, *, heartbeat: float = 15.0, max_duration: float = 1800.0
) -> AsyncIterator[str]:
    """Sinh frame SSE cho một kết nối; luôn trả slot khi kết thúc (kể cả client ngắt)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_duration
    try:
        yield "retry: 3000\n\n"
        # Giới hạn thời lượng: client kết nối lại => xác thực lại (session bị thu hồi không
        # giữ stream mãi).
        while (remaining := deadline - loop.time()) > 0:
            try:
                item = await asyncio.wait_for(sub.queue.get(), timeout=min(heartbeat, remaining))
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if item is OVERFLOW:
                yield RESYNC_FRAME
                return
            if item is RESYNC:
                yield RESYNC_FRAME
                continue
            try:
                signal = json.loads(item)
                event_id = int(signal.pop("id"))
            except (ValueError, KeyError, TypeError):
                continue  # tín hiệu hỏng: bỏ qua, không làm chết stream
            data = json.dumps(signal, separators=(",", ":"))
            yield f"event: signal\nid: {event_id}\ndata: {data}\n\n"
    finally:
        hub.close(sub)
