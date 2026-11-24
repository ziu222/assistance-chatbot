"""FailoverLLM: một LLMPort bọc nhiều backend, đi qua route theo thứ tự (ADR-0015).

Quy tắc phân loại lỗi (đây là chỗ dễ sai nhất):
- ProviderUnavailable (timeout, 5xx, quá tải, 429, key hỏng) ⇒ ghi nhận rồi thử đích kế.
- 429 ⇒ chờ (Retry-After nếu có, không thì lùi dần) rồi thử lại CÙNG đích tối đa 2 lần, trong trần
  timeout của đích; hết lượt mới tính là lỗi. Gateway giới hạn tốc độ khi nhiều run chạy cùng lúc:
  không chờ thì 3 lần 429 liên tiếp mở circuit và mọi run sau đó thất bại ngay (đo 2026-11-24).
- BadRequest (yêu cầu sai, vượt context) ⇒ ném ngay: đích khác cũng sẽ lỗi, đó là bug cần sửa.
- refusal ⇒ KHÔNG phải lỗi: trả về như kết quả, không đi tìm model chịu trả lời (§19.7).
- InvalidOutput ⇒ thử lại MỘT lần trên cùng đích rồi mới sang đích kế; không tính vào circuit.
Mọi đích nhận cùng một LLMRequest trung lập nên tri thức trong request không đổi (ADR-0030).
"""

import asyncio
import dataclasses
import logging
import random
import time
from collections.abc import Awaitable, Callable

from app.modules.agent_runtime.llm.breaker import CircuitBreaker
from app.modules.agent_runtime.llm.port import (
    AllTargetsFailed,
    Backend,
    BadRequest,
    DeadlineExceeded,
    FailedAttempt,
    InvalidOutput,
    LLMRequest,
    LLMResult,
    ProviderUnavailable,
    Target,
)
from app.modules.agent_runtime.llm.routes import Routes
from app.shared.infrastructure.metrics import (
    LLM_CIRCUIT_OPEN,
    LLM_FAILOVER,
    LLM_LATENCY,
    LLM_REQUESTS,
)

log = logging.getLogger(__name__)

RATE_LIMIT_RETRIES = 2
RATE_LIMIT_BACKOFF_S = (1.5, 4.0)  # khi không có Retry-After; có jitter để các run không dồn lại
MAX_RETRY_AFTER_S = 10.0


class FailoverLLM:
    def __init__(
        self,
        routes: Routes,
        backends: dict[str, Backend],
        *,
        breaker: CircuitBreaker | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._routes = routes
        self._backends = backends
        self._breaker = breaker or CircuitBreaker(clock=clock)
        self._clock = clock
        self._sleep = sleep

    async def complete(self, req: LLMRequest) -> LLMResult:
        targets = self._routes.get(req.route)
        if targets is None:
            raise BadRequest(f"unknown route {req.route!r}")

        attempts: list[FailedAttempt] = []
        for target in targets:
            provider = target.provider
            remaining = None if req.deadline is None else req.deadline - self._clock()
            if remaining is not None and remaining <= 0:
                raise DeadlineExceeded(f"run deadline passed after {len(attempts)} attempt(s)")
            if not self._breaker.allow(provider):
                self._observe(target, "circuit_open")
                attempts.append(FailedAttempt(target, "circuit_open"))
                continue

            timeout = target.timeout_s if remaining is None else min(target.timeout_s, remaining)
            started = self._clock()
            try:
                result = await self._call(self._backends[provider], target, req, timeout)
            except ProviderUnavailable as exc:
                self._breaker.failure(provider, exc.reason)
                self._observe(target, "provider_error")
                attempts.append(FailedAttempt(target, exc.reason))
                # Chỉ provider + lý do: không log prompt, key hay nội dung khách.
                log.warning(
                    "llm provider unavailable",
                    extra={"provider": provider, "model": target.model, "reason": exc.reason},
                )
                continue
            except InvalidOutput:
                self._breaker.release(provider)  # model trả rác không phải provider sập
                self._observe(target, "invalid_output")
                attempts.append(FailedAttempt(target, "invalid_output"))
                continue
            except BadRequest:
                self._breaker.release(provider)
                self._observe(target, "bad_request")
                raise

            self._breaker.success(provider)
            self._observe(target, "ok")
            LLM_LATENCY.labels(provider=provider).observe(self._clock() - started)
            if attempts:
                last = attempts[-1]
                LLM_FAILOVER.labels(
                    from_provider=last.target.provider, to_provider=provider, reason=last.reason
                ).inc()
            return dataclasses.replace(result, served_by=target, failover_from=tuple(attempts))

        raise AllTargetsFailed(tuple(attempts))

    async def _call(
        self, backend: Backend, target: Target, req: LLMRequest, timeout: float
    ) -> LLMResult:
        try:
            async with asyncio.timeout(timeout):  # trần cứng, bất kể SDK có tôn trọng timeout
                invalid, limited = 0, 0
                while True:
                    try:
                        return await backend.complete(target, req, timeout)
                    except InvalidOutput:
                        invalid += 1
                        if invalid == 2:
                            raise
                    except ProviderUnavailable as exc:
                        if exc.reason != "rate_limited" or limited == RATE_LIMIT_RETRIES:
                            raise
                        wait = exc.retry_after or RATE_LIMIT_BACKOFF_S[limited]
                        limited += 1
                        await self._sleep(min(wait, MAX_RETRY_AFTER_S) * random.uniform(1, 1.3))
        except TimeoutError:
            raise ProviderUnavailable(target.provider, "timeout") from None
        raise AssertionError("unreachable")

    def _observe(self, target: Target, outcome: str) -> None:
        LLM_REQUESTS.labels(provider=target.provider, model=target.model, outcome=outcome).inc()
        LLM_CIRCUIT_OPEN.labels(provider=target.provider).set(
            1 if self._breaker.is_open(target.provider) else 0
        )
