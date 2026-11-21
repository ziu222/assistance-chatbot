"""Agent worker: lấy yêu cầu run đã tới hạn và giao cho handler (ADR-0008).

Bước 2.1 chỉ có khung (claim, lease, retry, fencing); handler thật là agent runtime ở bước 2.5.
Chạy được nhiều instance và nhiều vòng song song: SKIP LOCKED + lease bảo đảm mỗi hội thoại
chỉ một run tại một thời điểm.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.agent_runtime.infrastructure.run_requests import (
    DebounceWindow,
    RunRequest,
    claim_next,
    complete,
    retry_later,
)
from app.shared.infrastructure.metrics import (
    AGENT_RUN_REQUEST_LAG,
    AGENT_RUN_REQUESTS,
    DEBOUNCE_COALESCED,
)

log = logging.getLogger(__name__)

RunHandler = Callable[[RunRequest], Awaitable[None]]

# Handler phải xong trước khi lease hết hạn, nếu không worker khác sẽ chạy trùng.
_LEASE_SAFETY = 0.8


class AgentWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        handler: RunHandler,
        *,
        window: DebounceWindow,
        lease: timedelta,
        max_attempts: int,
        retry_base: float = 2.0,
        idle_sleep: float = 0.5,
    ) -> None:
        self._factory = session_factory
        self._handler = handler
        self._window = window
        self._lease = lease
        self._max_attempts = max_attempts
        self._retry_base = retry_base
        self._idle_sleep = idle_sleep

    async def process_once(self) -> bool:
        """Xử lý tối đa một yêu cầu. Trả True nếu đã nhận được việc."""
        async with self._factory() as s, s.begin():  # transaction ngắn: chỉ để claim
            req = await claim_next(s, self._lease)
        if req is None:
            return False

        AGENT_RUN_REQUEST_LAG.observe(max(0.0, (datetime.now(UTC) - req.due_at).total_seconds()))
        DEBOUNCE_COALESCED.observe(req.to_sequence - req.from_sequence + 1)
        try:
            # Gọi model có thể lâu: chạy NGOÀI transaction, được bảo vệ bằng lease.
            async with asyncio.timeout(self._lease.total_seconds() * _LEASE_SAFETY):
                await self._handler(req)
        except Exception as exc:  # BaseException (hủy/tắt) để lease tự hết hạn
            delay = self._retry_base * 2 ** (req.attempts - 1) * random.uniform(0.5, 1.0)
            async with self._factory() as s, s.begin():
                outcome = await retry_later(
                    s, req, timedelta(seconds=delay), max_attempts=self._max_attempts
                )
            outcome = "retry" if outcome == "rescheduled" else outcome
            # Chỉ ID và loại lỗi; không log nội dung hội thoại.
            log.warning(
                "agent run failed",
                extra={
                    "conversation_id": str(req.conversation_id),
                    "attempts": req.attempts,
                    "error_type": type(exc).__name__,
                    "outcome": outcome,
                },
            )
        else:
            async with self._factory() as s, s.begin():
                outcome = await complete(s, req, self._window)
            if outcome == "lost":
                log.warning(
                    "agent run lease lost", extra={"conversation_id": str(req.conversation_id)}
                )
        AGENT_RUN_REQUESTS.labels(outcome=outcome).inc()
        return True

    async def run(self, stop: asyncio.Event, concurrency: int = 4) -> None:
        """`concurrency` vòng song song trong một process (I/O-bound: chờ model là chính)."""

        async def loop() -> None:
            while not stop.is_set():
                try:
                    busy = await self.process_once()
                except Exception:
                    log.exception("agent worker iteration crashed")
                    busy = False
                if not busy:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=self._idle_sleep)
                    except TimeoutError:
                        pass

        async with asyncio.TaskGroup() as tg:
            for _ in range(concurrency):
                tg.create_task(loop())
