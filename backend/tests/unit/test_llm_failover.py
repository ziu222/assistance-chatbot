"""FailoverLLM + CircuitBreaker (ADR-0015): chuyển đích đúng lúc, KHÔNG chuyển sai lúc."""

import asyncio
import time

import pytest
from prometheus_client import REGISTRY

from app.modules.agent_runtime.llm.breaker import CircuitBreaker
from app.modules.agent_runtime.llm.failover import FailoverLLM
from app.modules.agent_runtime.llm.fake import (
    FakeBackend,
    refusal_result,
    text_result,
)
from app.modules.agent_runtime.llm.port import (
    AllTargetsFailed,
    BadRequest,
    DeadlineExceeded,
    InvalidOutput,
    LLMRequest,
    ProviderUnavailable,
    Target,
    Turn,
)

A = Target("anthropic", "claude-sonnet-5", max_tokens=1024, effort="low")
O = Target("openai", "gpt-4.1-mini", max_tokens=512)  # noqa: E741


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def request(**over) -> LLMRequest:
    base = dict(route="draft", system_blocks=("FAQ: đổi trả 7 ngày",), turns=(Turn("user", "hi"),))
    return LLMRequest(**(base | over))


def make(a_script, o_script, *, breaker=None):
    a, o = FakeBackend("anthropic", a_script), FakeBackend("openai", o_script)
    routes = {"draft": (A, O), "classify": (A, O)}
    llm = FailoverLLM(routes, {"anthropic": a, "openai": o}, breaker=breaker, sleep=_no_sleep)
    return llm, a, o


async def _no_sleep(_: float) -> None:
    return None


def metric(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_primary_serves_and_backup_is_untouched():
    llm, a, o = make([text_result("từ claude")], [])
    result = await llm.complete(request())
    assert (result.text, result.served_by, result.failover_from) == ("từ claude", A, ())
    assert o.calls == []


@pytest.mark.parametrize(
    "reason", ["timeout", "connection", "server_error", "overloaded", "rate_limited", "auth"]
)
async def test_provider_trouble_fails_over_and_records_why(reason):
    # 429 được thử lại 2 lần trên cùng đích trước khi tính là lỗi.
    tries = 3 if reason == "rate_limited" else 1
    llm, a, o = make([ProviderUnavailable("anthropic", reason)] * tries, [text_result("từ openai")])
    before = metric(
        "llm_failover_total", from_provider="anthropic", to_provider="openai", reason=reason
    )
    result = await llm.complete(request())

    assert (result.text, result.served_by.provider) == ("từ openai", "openai")
    assert [(f.target, f.reason) for f in result.failover_from] == [(A, reason)]
    after = metric(
        "llm_failover_total", from_provider="anthropic", to_provider="openai", reason=reason
    )
    assert after == before + 1


async def test_bad_request_is_not_retried_elsewhere():
    llm, a, o = make([BadRequest("context too long")], [text_result("không được tới đây")])
    with pytest.raises(BadRequest):
        await llm.complete(request())
    assert o.calls == []  # đích khác cũng sẽ lỗi: đây là bug cần sửa, không phải sự cố provider


async def test_refusal_is_returned_not_shopped_around():
    llm, a, o = make([refusal_result()], [text_result("model khác chịu trả lời")])
    result = await llm.complete(request())
    assert result.stop_reason == "refusal" and result.failover_from == ()
    assert o.calls == []  # §19.7: không đi tìm model chịu trả lời


async def test_invalid_output_retries_same_target_once():
    llm, a, o = make([InvalidOutput("bad json"), text_result("lần 2 ổn")], [])
    result = await llm.complete(request())
    assert result.served_by == A and len(a.calls) == 2 and o.calls == []


async def test_invalid_output_twice_fails_over_without_tripping_the_circuit():
    breaker = CircuitBreaker(threshold=1)  # ngưỡng 1: chỉ một lỗi "provider" là mở
    llm, a, o = make(
        [InvalidOutput("x"), InvalidOutput("y"), text_result("lượt sau")],
        [text_result("openai cứu")],
        breaker=breaker,
    )
    first = await llm.complete(request())
    assert first.served_by.provider == "openai"
    assert [f.reason for f in first.failover_from] == ["invalid_output"]
    second = await llm.complete(request())
    assert second.served_by == A  # model trả rác không phải provider sập: circuit vẫn đóng


async def test_all_targets_failing_raises_with_every_attempt():
    llm, a, o = make(
        [ProviderUnavailable("anthropic", "timeout")],
        [ProviderUnavailable("openai", "server_error")],
    )
    with pytest.raises(AllTargetsFailed) as e:
        await llm.complete(request())
    assert [(x.target.provider, x.reason) for x in e.value.attempts] == [
        ("anthropic", "timeout"),
        ("openai", "server_error"),
    ]


async def test_unknown_route_is_a_bad_request():
    llm, *_ = make([], [])
    with pytest.raises(BadRequest, match="vip"):
        await llm.complete(request(route="vip"))


async def test_failover_sends_the_identical_neutral_request():
    llm, a, o = make([ProviderUnavailable("anthropic", "timeout")], [text_result("ok")])
    req = request()
    await llm.complete(req)
    assert a.calls[0].request is req and o.calls[0].request is req
    # → tri thức (system_blocks) tới đích dự phòng nguyên vẹn (ADR-0030)
    assert o.calls[0].request.system_blocks == ("FAQ: đổi trả 7 ngày",)


# ---------- circuit breaker ----------


async def test_circuit_opens_after_three_failures_then_skips_without_calling():
    clock = Clock()
    breaker = CircuitBreaker(clock=clock)
    fail = lambda: ProviderUnavailable("anthropic", "server_error")  # noqa: E731
    llm, a, o = make([fail(), fail(), fail()], [text_result("o")] * 5, breaker=breaker)

    for _ in range(3):
        await llm.complete(request())
    assert len(a.calls) == 3 and metric("llm_circuit_open", provider="anthropic") == 1

    result = await llm.complete(request())  # circuit mở: không gọi anthropic nữa
    assert len(a.calls) == 3
    assert [f.reason for f in result.failover_from] == ["circuit_open"]


async def test_half_open_lets_one_trial_through_and_recovers_on_success():
    clock = Clock()
    breaker = CircuitBreaker(clock=clock)
    llm, a, o = make(
        [ProviderUnavailable("anthropic", "timeout")] * 3
        + [text_result("đã hồi phục"), text_result("vẫn ổn")],
        [text_result("o")] * 5,
        breaker=breaker,
    )
    for _ in range(3):
        await llm.complete(request())
    clock.now += 31  # hết 30 s

    result = await llm.complete(request())  # đúng một yêu cầu thật đi thử
    assert result.served_by == A and result.text == "đã hồi phục"
    assert metric("llm_circuit_open", provider="anthropic") == 0
    assert (await llm.complete(request())).served_by == A  # đã đóng lại hẳn


async def test_failed_trial_reopens_for_another_window():
    clock = Clock()
    breaker = CircuitBreaker(clock=clock)
    llm, a, o = make(
        [ProviderUnavailable("anthropic", "timeout")] * 4, [text_result("o")] * 6, breaker=breaker
    )
    for _ in range(3):
        await llm.complete(request())
    clock.now += 31
    await llm.complete(request())  # lượt thử, vẫn lỗi
    calls = len(a.calls)
    await llm.complete(request())  # lại mở: không gọi
    assert len(a.calls) == calls


async def test_half_open_allows_exactly_one_concurrent_trial():
    clock = Clock()
    breaker = CircuitBreaker(clock=clock)
    started = 0

    async def slow(req):
        nonlocal started
        started += 1
        await asyncio.sleep(0.05)
        return text_result("trial ok")

    llm, a, o = make(
        [ProviderUnavailable("anthropic", "timeout")] * 3 + [slow],
        [text_result("o")] * 10,
        breaker=breaker,
    )
    for _ in range(3):
        await llm.complete(request())
    clock.now += 31

    results = await asyncio.gather(*(llm.complete(request()) for _ in range(5)))
    assert started == 1  # không để cả đàn request đập vào provider vừa hồi
    assert sorted(r.served_by.provider for r in results) == ["anthropic"] + ["openai"] * 4


@pytest.mark.parametrize("reason", ["auth", "config", "quota"])
async def test_persistent_failures_open_immediately_for_ten_minutes(reason):
    clock = Clock()
    breaker = CircuitBreaker(clock=clock)
    llm, a, o = make(
        [ProviderUnavailable("anthropic", reason), text_result("sống lại")],
        [text_result("o")] * 5,
        breaker=breaker,
    )
    await llm.complete(request())
    assert metric("llm_circuit_open", provider="anthropic") == 1  # key hỏng không tự lành
    clock.now += 60
    assert (await llm.complete(request())).served_by.provider == "openai"  # vẫn mở sau 1 phút
    clock.now += 600
    assert (await llm.complete(request())).served_by == A


async def test_success_resets_the_failure_streak():
    breaker = CircuitBreaker(clock=Clock())
    fail = ProviderUnavailable("anthropic", "timeout")
    llm, a, o = make(
        [fail, fail, text_result("ok"), fail, fail, text_result("ok2")],
        [text_result("o")] * 6,
        breaker=breaker,
    )
    for _ in range(6):
        await llm.complete(request())
    assert metric("llm_circuit_open", provider="anthropic") == 0  # không bao giờ đủ 3 lỗi LIÊN TIẾP


async def test_bad_request_during_trial_releases_the_trial_slot():
    clock = Clock()
    breaker = CircuitBreaker(clock=clock)
    llm, a, o = make(
        [ProviderUnavailable("anthropic", "timeout")] * 3 + [BadRequest("x"), text_result("ok")],
        [text_result("o")] * 5,
        breaker=breaker,
    )
    for _ in range(3):
        await llm.complete(request())
    clock.now += 31
    with pytest.raises(BadRequest):
        await llm.complete(request())
    # lượt thử bị lỗi yêu cầu không được làm kẹt trạng thái nửa mở mãi mãi
    assert (await llm.complete(request())).served_by == A


# ---------- thời gian ----------


async def test_slow_provider_is_cut_at_its_target_timeout_then_failover():
    async def slow(req):
        await asyncio.sleep(5)
        return text_result("quá trễ")

    slow_target = Target("anthropic", "claude-sonnet-5", max_tokens=1024, timeout_s=0.2)
    a = FakeBackend("anthropic", [slow])
    o = FakeBackend("openai", [text_result("kịp")])
    llm = FailoverLLM({"draft": (slow_target, O)}, {"anthropic": a, "openai": o})

    started = time.monotonic()
    result = await llm.complete(request(deadline=started + 10))
    elapsed = time.monotonic() - started

    assert result.served_by.provider == "openai" and elapsed < 1.0
    assert [f.reason for f in result.failover_from] == ["timeout"]


async def test_target_timeout_is_capped_by_remaining_run_time():
    target = Target("anthropic", "claude-sonnet-5", max_tokens=1024, timeout_s=30.0)
    a = FakeBackend("anthropic", [text_result("ok")])
    llm = FailoverLLM({"draft": (target,)}, {"anthropic": a})

    await llm.complete(request(deadline=time.monotonic() + 2.0))
    assert a.calls[0].timeout <= 2.0  # không bao giờ chờ lâu hơn ngân sách còn lại của run


async def test_expired_deadline_stops_before_calling_anyone():
    llm, a, o = make([text_result("x")], [text_result("y")])
    with pytest.raises(DeadlineExceeded):
        await llm.complete(request(deadline=time.monotonic() - 1))
    assert a.calls == [] and o.calls == []


async def test_deadline_runs_out_between_targets():
    async def burn(req):
        await asyncio.sleep(0.3)
        raise ProviderUnavailable("anthropic", "timeout")

    llm, a, o = make([burn], [text_result("không kịp")])
    with pytest.raises(DeadlineExceeded):
        await llm.complete(request(deadline=time.monotonic() + 0.25))
    assert o.calls == []  # hết ngân sách thì dừng, không gọi tiếp


async def test_rate_limit_waits_and_retries_the_same_target():
    waits: list[float] = []

    async def sleep(s: float) -> None:
        waits.append(s)

    a = FakeBackend(
        "anthropic",
        [
            ProviderUnavailable("anthropic", "rate_limited", 429, retry_after=2.0),
            ProviderUnavailable("anthropic", "rate_limited", 429),
            text_result("từ claude"),
        ],
    )
    o = FakeBackend("openai", [])
    llm = FailoverLLM({"draft": (A, O)}, {"anthropic": a, "openai": o}, sleep=sleep)
    labels = dict(provider="anthropic", model=A.model, outcome="rate_limited_retry")
    before = metric("llm_requests_total", **labels)
    result = await llm.complete(request())
    assert metric("llm_requests_total", **labels) == before + 2
    assert (result.text, result.failover_from) == ("từ claude", ())
    assert len(a.calls) == 3 and o.calls == []
    assert 2.0 <= waits[0] <= 2.6 and 4.0 <= waits[1] <= 5.2  # Retry-After, rồi lùi dần


async def test_rate_limit_retries_do_not_trip_the_circuit_when_they_succeed():
    breaker = CircuitBreaker()
    limited = [ProviderUnavailable("anthropic", "rate_limited")] * 2
    llm, a, _ = make([*limited, text_result("ok")] * 3, [], breaker=breaker)
    for _ in range(3):
        assert (await llm.complete(request())).text == "ok"
    assert not breaker.is_open("anthropic")
