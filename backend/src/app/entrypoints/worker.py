"""Chạy outbox relay: `uv run python -m app.entrypoints.worker`.

Bước 1 chạy đúng MỘT instance (xem docs/design/01-webchat-inbound-pipeline.md §8.4).
"""

import asyncio
import contextlib
import signal
from functools import partial

import redis.asyncio as aioredis
from prometheus_client import start_http_server

from app.config.settings import get_settings
from app.modules.agent_runtime.infrastructure.config import debounce_window
from app.modules.agent_runtime.infrastructure.run_requests import enqueue_from_outbox
from app.shared.infrastructure.db import create_engine, create_session_factory
from app.shared.infrastructure.logging import configure_logging
from app.workers.outbox_relay import OutboxRelay, RedisPublisher


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    client = aioredis.from_url(settings.redis_url)
    relay = OutboxRelay(
        create_session_factory(engine),
        RedisPublisher(client),
        # Tin khách ⇒ hàng đợi agent bền, cùng transaction với outbox (ADR-0008).
        on_published=partial(enqueue_from_outbox, window=debounce_window(settings)),
    )
    start_http_server(settings.worker_metrics_port)  # /metrics nội bộ của relay (lag, ...)

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
    configure_logging()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
