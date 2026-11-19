"""Chạy outbox relay: `uv run python -m app.entrypoints.worker`.

Bước 1 chạy đúng MỘT instance (xem docs/design/01-webchat-inbound-pipeline.md §8.4).
"""

import asyncio
import contextlib
import logging
import signal

import redis.asyncio as aioredis

from app.config.settings import get_settings
from app.shared.infrastructure.db import create_engine, create_session_factory
from app.workers.outbox_relay import OutboxRelay, RedisPublisher


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    client = aioredis.from_url(settings.redis_url)
    relay = OutboxRelay(create_session_factory(engine), RedisPublisher(client))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows không hỗ trợ add_signal_handler; Ctrl+C vẫn dừng qua KeyboardInterrupt.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    try:
        await relay.run(stop)
    finally:
        await client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
