"""Chạy agent worker: `uv run python -m app.entrypoints.agent_worker [--roles ...]`."""

# Vai trò hợp lệ: agent, authoring, judge (mặc định chạy tất cả).
# Thiếu route hoặc giá cho model trong các vai trò được bật ⇒ dừng ngay lúc khởi động.

import argparse
import asyncio
import contextlib
import logging
import signal
from collections.abc import Iterable
from datetime import timedelta

from prometheus_client import start_http_server

from app.config.settings import get_settings
from app.modules.agent_management.infrastructure.templates import load_templates
from app.modules.agent_runtime.infrastructure.config import debounce_window
from app.modules.agent_runtime.llm.factory import build_llm
from app.modules.agent_runtime.runner import AgentRunner
from app.modules.authoring.worker import AuthoringWorker
from app.modules.conversation_policy.stage import PolicyStage
from app.modules.learning.infrastructure.candidates import LearningWorker
from app.modules.shadow.infrastructure.judging import JudgeWorker
from app.shared.infrastructure.db import create_engine, create_session_factory
from app.shared.infrastructure.logging import configure_logging
from app.workers.agent_worker import AgentWorker

log = logging.getLogger(__name__)

ALL_ROLES: tuple[str, ...] = ("agent", "authoring", "judge", "learning")


def parse_roles(raw: str | None) -> tuple[str, ...]:
    """Chuẩn hóa danh sách vai trò worker từ `--roles`. Rỗng/None ⇒ chạy tất cả."""
    if not raw or not raw.strip():
        return ALL_ROLES
    seen: list[str] = []
    for part in raw.split(","):
        role = part.strip().lower()
        if not role:
            continue
        if role not in ALL_ROLES:
            raise ValueError(f"unknown worker role: {role!r} (valid: {', '.join(ALL_ROLES)})")
        if role not in seen:
            seen.append(role)
    if not seen:
        raise ValueError("at least one worker role is required")
    return tuple(seen)


def routes_for_roles(roles: Iterable[str]) -> dict[str, set[str]]:
    """Tập route LLM cần kiểm tra lúc khởi động, tương ứng với các vai trò được bật."""
    role_set = set(roles)
    used: dict[str, set[str]] = {}
    if "agent" in role_set:
        for t in load_templates().values():
            used[f"{t.key}.v{t.version}"] = {t.routes.classify, t.routes.draft}
    if "authoring" in role_set:
        used["authoring"] = {"authoring"}
    if "judge" in role_set:
        used["judge"] = {"judge"}
    if "learning" in role_set:
        used["learning"] = {"learning"}
    return used


async def main(roles: tuple[str, ...] = ALL_ROLES) -> None:
    settings = get_settings()
    llm, _ = build_llm(settings, routes_for_roles(roles))
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    start_http_server(settings.agent_worker_metrics_port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows: Ctrl+C qua KeyboardInterrupt
            loop.add_signal_handler(sig, stop.set)

    tasks = []
    if "agent" in roles:
        worker = AgentWorker(
            factory,
            PolicyStage(factory, AgentRunner(factory, llm).handle).handle,
            window=debounce_window(settings),
            lease=timedelta(seconds=settings.agent_lease_seconds),
            max_attempts=settings.agent_max_attempts,
        )
        tasks.append(worker.run(stop, concurrency=settings.agent_worker_concurrency))
    if "authoring" in roles:
        authoring = AuthoringWorker(
            factory, llm, daily_cap_micro=settings.authoring_daily_cap_micro
        )
        tasks.append(authoring.run(stop))
    if "judge" in roles:
        judge = JudgeWorker(
            factory,
            llm,
            daily_cap_micro=settings.judge_daily_cap_micro,
            full_per_day=settings.judge_full_per_day,
            per_intent_after=settings.judge_per_intent_after,
        )
        tasks.append(judge.run(stop))  # chấm nền so sánh shadow (thiết kế 04 S2)
    if "learning" in roles:
        learning = LearningWorker(factory, llm, daily_cap_micro=settings.learning_daily_cap_micro)
        tasks.append(learning.run(stop))  # gom bản ghi sửa thành ứng viên tri thức (05 L3)

    log.info("starting agent worker", extra={"roles": list(roles)})
    try:
        await asyncio.gather(*tasks)
    finally:
        await engine.dispose()


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Run Atlas background AI workers.")
    parser.add_argument(
        "--roles",
        default=None,
        help=f"Comma-separated roles to run (default: {','.join(ALL_ROLES)})",
    )
    args = parser.parse_args()
    roles = parse_roles(args.roles)
    configure_logging()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main(roles))


if __name__ == "__main__":
    _cli()
