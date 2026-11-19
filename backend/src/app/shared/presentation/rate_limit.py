from fastapi import Request

from app.shared.infrastructure.metrics import RATE_LIMITED
from app.shared.infrastructure.rate_limit import RateLimitUnavailable
from app.shared.presentation.http_errors import ApiError


async def enforce_rate_limit(
    request: Request, key: str, limit: int, *, scope: str, fail_open: bool
) -> None:
    """429 + Retry-After khi vượt; Redis lỗi + fail closed => 503. `scope`: nhãn metric."""
    try:
        retry_after = await request.app.state.rate_limiter.hit(key, limit, fail_open=fail_open)
    except RateLimitUnavailable as exc:
        raise ApiError(
            503, "rate_limit_unavailable", "Try again later", {"Retry-After": "5"}
        ) from exc
    if retry_after is not None:
        RATE_LIMITED.labels(scope=scope).inc()
        raise ApiError(429, "rate_limited", "Too many requests", {"Retry-After": str(retry_after)})
