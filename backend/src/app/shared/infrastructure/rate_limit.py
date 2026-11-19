"""Rate limit cửa sổ cố định trên Redis (INCR + EXPIRE NX).

ponytail: cửa sổ cố định cho phép burst gấp đôi ở ranh giới cửa sổ; chuyển sliding window
khi đo thấy cần (thiết kế §8.3).
"""

import hashlib
import time

from redis.exceptions import RedisError

LOCAL_MAX_KEYS = 10_000


class RateLimitUnavailable(Exception):
    """Redis lỗi và phạm vi này fail closed."""


def hashed(value: str) -> str:
    # Không để IP thô trong key Redis.
    return hashlib.sha256(value.encode()).hexdigest()[:16]


class RateLimiter:
    def __init__(self, redis, *, cooldown: float = 5.0) -> None:
        self._redis = redis
        self._cooldown = cooldown
        self._down_until = 0.0
        self._local: dict[str, tuple[int, int]] = {}  # key -> (window_index, count)

    async def hit(self, key: str, limit: int, window: int = 60, *, fail_open: bool) -> int | None:
        """Ghi một lượt. Trả số giây Retry-After nếu vượt giới hạn, None nếu còn được."""
        try:
            if time.monotonic() < self._down_until:
                # Ngắt mạch: Redis vừa lỗi, không bắt mọi request chờ timeout kết nối.
                raise RedisError("rate limiter circuit open")
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(f"rl:{key}")
                pipe.expire(f"rl:{key}", window, nx=True)
                pipe.ttl(f"rl:{key}")
                count, _, ttl = await pipe.execute()
        except (RedisError, OSError) as exc:
            self._down_until = max(self._down_until, time.monotonic() + self._cooldown)
            if not fail_open:
                raise RateLimitUnavailable from exc
            return self._hit_local(key, limit, window)
        return max(int(ttl), 1) if count > limit else None

    def _hit_local(self, key: str, limit: int, window: int) -> int | None:
        # Dự phòng theo từng process khi Redis lỗi: chặn flood thô, bộ nhớ có biên.
        now = time.time()
        index = int(now // window)
        if len(self._local) > LOCAL_MAX_KEYS:
            self._local.clear()
        prev_index, count = self._local.get(key, (index, 0))
        count = count + 1 if prev_index == index else 1
        self._local[key] = (index, count)
        return max(int((index + 1) * window - now), 1) if count > limit else None
