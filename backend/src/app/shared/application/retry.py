import asyncio
import random
from collections.abc import Awaitable, Callable

from app.shared.application.errors import RetryableConflict, TransientDatabaseError
from app.shared.infrastructure.metrics import DB_TX_RETRIES  # metric là mối quan tâm cắt ngang

MAX_ATTEMPTS = 3


async def retry_conflicts[T](attempt: Callable[[], Awaitable[T]]) -> T:
    """Chạy lại transaction khi DB báo xung đột tạm thời (deadlock/serialization)."""
    for n in range(1, MAX_ATTEMPTS + 1):
        try:
            return await attempt()
        except RetryableConflict as exc:
            if n == MAX_ATTEMPTS:
                raise TransientDatabaseError("conflict persisted after retries") from exc
            DB_TX_RETRIES.inc()
            await asyncio.sleep(random.uniform(0, 0.02 * 2**n))
    raise AssertionError("unreachable")
