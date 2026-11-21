"""Bản giả cho test: kịch bản có sẵn, không mạng, không tốn tiền (CI không cần API key)."""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.modules.agent_runtime.llm.port import (
    LLMRequest,
    LLMResult,
    Target,
    ToolCall,
    Usage,
)

FAKE_TARGET = Target("fake", "fake-model", max_tokens=1024)

# Một bước kịch bản: kết quả, lỗi để ném, hoặc hàm nhìn request rồi trả kết quả/ném lỗi.
Step = LLMResult | BaseException | Callable[[LLMRequest], LLMResult | Awaitable[LLMResult]]


def text_result(text: str, *, usage: Usage | None = None, parsed=None) -> LLMResult:
    return LLMResult("end_turn", text, parsed, (), usage or Usage(input=10, output=5), FAKE_TARGET)


def tool_call_result(*calls: ToolCall, usage: Usage | None = None) -> LLMResult:
    return LLMResult("tool_use", None, None, calls, usage or Usage(input=10, output=5), FAKE_TARGET)


def refusal_result() -> LLMResult:
    return LLMResult("refusal", None, None, (), Usage(input=10, output=0), FAKE_TARGET)


async def _next(script: list[Step], req: LLMRequest) -> LLMResult:
    assert script, "FakeLLM: script is empty (the code made more calls than the test scripted)"
    step = script.pop(0)
    if isinstance(step, BaseException):
        raise step
    result = step(req) if callable(step) else step
    return await result if inspect.isawaitable(result) else result


class FakeLLM:
    """`LLMPort` cho test runtime: trả kịch bản theo thứ tự và ghi lại mọi request."""

    def __init__(self, script: list[Step]) -> None:
        self._script = list(script)
        self.requests: list[LLMRequest] = []

    async def complete(self, req: LLMRequest) -> LLMResult:
        self.requests.append(req)
        return await _next(self._script, req)


@dataclass(frozen=True)
class Call:
    target: Target
    request: LLMRequest
    timeout: float


class FakeBackend:
    """`Backend` giả cho test failover: mỗi provider một kịch bản riêng."""

    def __init__(self, provider: str, script: list[Step]) -> None:
        self.provider = provider
        self._script = list(script)
        self.calls: list[Call] = []

    async def complete(self, target: Target, req: LLMRequest, timeout: float) -> LLMResult:
        self.calls.append(Call(target, req, timeout))
        result = await _next(self._script, req)
        # Như adapter thật: kết quả luôn ghi đích đã phục vụ.
        return LLMResult(**{**result.__dict__, "served_by": target})
