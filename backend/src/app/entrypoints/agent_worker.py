"""Chạy agent worker: `uv run python -m app.entrypoints.agent_worker`.

Handler: tầng chính sách hội thoại (2.7) rồi agent runtime (2.5). Thiếu route hoặc giá cho
model trong route ⇒ dừng ngay lúc khởi động, không đợi khách đầu tiên nhắn tin.
"""

import asyncio
import contextlib
import logging
import signal
from datetime import timedelta

from prometheus_client import start_http_server

from app.config.settings import get_settings
from app.modules.agent_management.infrastructure.templates import load_templates
from app.modules.agent_runtime.infrastructure.config import debounce_window
from app.modules.agent_runtime.llm.factory import build_llm
from app.modules.agent_runtime.runner import AgentRunner
from app.modules.authoring.worker import AuthoringWorker
from app.modules.conversation_policy.stage import PolicyStage
from app.shared.infrastructure.db import create_engine, create_session_factory
from app.shared.infrastructure.logging import configure_logging
from app.workers.agent_worker import AgentWorker

log = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    used_routes = {
        f"{t.key}.v{t.version}": {t.routes.classify, t.routes.draft}
        for t in load_templates().values()
    }
    used_routes["authoring"] = {"authoring"}
    llm, _ = build_llm(settings, used_routes)
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    worker = AgentWorker(
        factory,
        PolicyStage(factory, AgentRunner(factory, llm).handle).handle,
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
        authoring = AuthoringWorker(
            factory, llm, daily_cap_micro=settings.authoring_daily_cap_micro
        )
        await asyncio.gather(
            worker.run(stop, concurrency=settings.agent_worker_concurrency),
            authoring.run(stop),
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    configure_logging()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
