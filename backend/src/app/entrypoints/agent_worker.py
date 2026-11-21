"""Chạy agent worker: `uv run python -m app.entrypoints.agent_worker`.

Bước 2.1: handler chỉ ghi log (chưa gọi model). Agent runtime thật thay vào ở bước 2.5.
"""

import asyncio
import contextlib
import logging
import signal
from datetime import timedelta

from prometheus_client import start_http_server

from app.config.settings import get_settings
from app.modules.agent_runtime.infrastructure.config import debounce_window
from app.modules.agent_runtime.infrastructure.run_requests import RunRequest
from app.shared.infrastructure.db import create_engine, create_session_factory
from app.shared.infrastructure.logging import configure_logging
from app.workers.agent_worker import AgentWorker

log = logging.getLogger(__name__)


async def log_only_handler(req: RunRequest) -> None:
    log.info(
        "agent run (no-op)",
        extra={
            "conversation_id": str(req.conversation_id),
            "from_sequence": req.from_sequence,
            "to_sequence": req.to_sequence,
            "attempts": req.attempts,
        },
    )


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    worker = AgentWorker(
        create_session_factory(engine),
        log_only_handler,
        window=debounce_window(settings),
        lease=timedelta(seconds=settings.agent_lease_seconds),
        max_attempts=settings.agent_max_attempts,
    )
    start_http_server(settings.agent_worker_metrics_port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows: Ctrl+C qua KeyboardInterrupt
            loop.add_signal_handler(sig, stop.set)
    try:
        await worker.run(stop, concurrency=settings.agent_worker_concurrency)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    configure_logging()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
