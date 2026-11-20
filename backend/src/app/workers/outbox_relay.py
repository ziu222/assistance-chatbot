"""Outbox relay: đọc outbox_events chưa publish, đẩy TÍN HIỆU (chỉ ID) lên Redis pub/sub.

Bảo đảm: at-least-once. Crash giữa publish và commit -> publish lại; client idempotent theo
(conversation_id, sequence) nên tín hiệu trùng vô hại. Redis pub/sub bản thân là at-most-once
(subscriber mất kết nối sẽ lỡ tín hiệu) -> client luôn refetch REST khi kết nối lại (thiết kế §7.4).
Consumer cần độ bền (agent trigger, bước 2) KHÔNG dùng kênh này.
"""

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.shared.infrastructure.metrics import OUTBOX_PUBLISH_LAG
from app.shared.infrastructure.outbox import OutboxEventModel

log = logging.getLogger(__name__)


class EventPublisher(Protocol):
    async def publish(self, channel: str, message: str) -> None: ...


class RedisPublisher:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def publish(self, channel: str, message: str) -> None:
        await self._client.publish(channel, message)


def channels_for(payload: dict[str, Any]) -> list[str]:
    channels = [f"evt:t:{payload['tenant_id']}"]
    if payload.get("visitor_id") and payload.get("connection_id"):
        channels.append(f"evt:v:{payload['connection_id']}:{payload['visitor_id']}")
    return channels


def signal_for(event_id: int, event_type: str, payload: dict[str, Any]) -> str:
    signal = {
        "id": event_id,
        "t": event_type,
        "c": payload.get("conversation_id"),
        "s": payload.get("sequence"),
    }
    return json.dumps(signal, separators=(",", ":"))


@dataclass
class RelayStats:
    published: int = 0
    failed: int = 0
    last_lag_seconds: float = 0.0


class OutboxRelay:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
        *,
        batch_size: int = 100,
        idle_sleep: float = 0.2,
        max_backoff: float = 5.0,
    ) -> None:
        self._factory = session_factory
        self._publisher = publisher
        self._batch_size = batch_size
        self._idle_sleep = idle_sleep
        self._max_backoff = max_backoff
        self._last_batch_failed = False
        self.stats = RelayStats()

    async def run_once(self) -> int:
        """Một batch trong một transaction. Trả số event đã publish."""
        self._last_batch_failed = False
        done: list[OutboxEventModel] = []
        async with self._factory() as session, session.begin():
            # SKIP LOCKED: nhiều relay không tranh nhau dòng, nhưng mất thứ tự toàn cục.
            # Bước 1 chạy một relay; thứ tự hiển thị dựa trên sequence, không dựa vào đây.
            rows = (
                await session.execute(
                    select(OutboxEventModel)
                    .where(OutboxEventModel.published_at.is_(None))
                    .order_by(OutboxEventModel.id)
                    .limit(self._batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
            for row in rows:
                try:
                    message = signal_for(row.id, row.event_type, row.payload)
                    for channel in channels_for(row.payload):
                        await self._publisher.publish(channel, message)
                except Exception as exc:  # BaseException (crash/cancel) -> rollback cả batch
                    row.attempts += 1
                    row.last_error = f"{type(exc).__name__}: {exc}"[:500]
                    self._last_batch_failed = True
                    self.stats.failed += 1
                    # Không log payload: chỉ ID và loại lỗi.
                    log.warning(
                        "outbox publish failed",
                        extra={"outbox_id": row.id, "error_type": type(exc).__name__},
                    )
                    break  # lỗi hạ tầng thường kéo dài; dừng batch, để backoff xử lý
                done.append(row)

            if done:
                await session.execute(
                    update(OutboxEventModel)
                    .where(OutboxEventModel.id.in_([r.id for r in done]))
                    .values(published_at=func.now())
                    .execution_options(synchronize_session=False)
                )

        if done:
            now = datetime.now(UTC)
            lags = [(now - r.created_at).total_seconds() for r in done]
            for lag in lags:
                OUTBOX_PUBLISH_LAG.observe(lag)
            self.stats.published += len(done)
            self.stats.last_lag_seconds = max(lags)
            log.info(
                "outbox published",
                extra={"count": len(done), "max_lag_seconds": self.stats.last_lag_seconds},
            )
        return len(done)

    async def run(self, stop: asyncio.Event) -> None:
        backoff = 0.0
        while not stop.is_set():
            try:
                published = await self.run_once()
                failed = self._last_batch_failed
            except Exception:
                log.exception("outbox relay batch crashed")
                published, failed = 0, True

            if failed:
                backoff = min(self._max_backoff, backoff * 2 or self._idle_sleep)
                delay = random.uniform(backoff / 2, backoff)  # jitter
            else:
                backoff = 0.0
                # Còn việc thì chạy tiếp ngay; rảnh thì ngủ.
                # Giới hạn đã biết: poll gây trễ tối đa idle_sleep. Nâng cấp: pg_notify đánh thức.
                delay = 0.0 if published else self._idle_sleep

            if delay:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass


async def outbox_backlog(session: AsyncSession) -> tuple[int, float]:
    """(số event chưa publish, tuổi event cũ nhất tính bằng giây) — chỉ số để cảnh báo."""
    row = (
        await session.execute(
            text(
                "SELECT count(*), "
                "coalesce(extract(epoch FROM now() - min(created_at)), 0) "
                "FROM outbox_events WHERE published_at IS NULL"
            )
        )
    ).one()
    return int(row[0]), float(row[1])


async def purge_published(session: AsyncSession, *, older_than: timedelta) -> int:
    """Xóa event đã publish cũ hơn ngưỡng. Event chưa publish không bao giờ bị xóa."""
    result = await session.execute(
        delete(OutboxEventModel).where(
            OutboxEventModel.published_at.is_not(None),
            OutboxEventModel.published_at < func.now() - older_than,
        )
    )
    return result.rowcount or 0
