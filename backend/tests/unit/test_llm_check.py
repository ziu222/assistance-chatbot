"""Kiểm thử hàm `probe_structured` trong `entrypoints.llm_check` với LLM giả."""

import pytest

from app.entrypoints.llm_check import _authoring_request, probe_structured
from app.modules.agent_runtime.llm.fake import FakeLLM
from app.modules.agent_runtime.llm.port import (
    AllTargetsFailed,
    FailedAttempt,
    InvalidOutput,
    LLMResult,
    ProviderUnavailable,
    Target,
    Usage,
)
from app.modules.authoring.domain.writing import Suggestion, WritingOut

_TARGET = Target("anthropic", "claude-haiku-5-5", max_tokens=2048)


def _ok_result() -> LLMResult:
    parsed = WritingOut(
        suggestions=[Suggestion(text="Shop bán bình giữ nhiệt, giao 2-4 ngày.", changes="Gọn hơn")]
    )
    return LLMResult("end_turn", "", parsed, (), Usage(input=50, output=30), _TARGET)


@pytest.mark.asyncio
async def test_probe_structured_counts_parsed_and_errors():
    llm = FakeLLM(
        [
            _ok_result(),
            InvalidOutput("anthropic", "bad json"),
            AllTargetsFailed((FailedAttempt(_TARGET, "invalid_output"),)),
            ProviderUnavailable("anthropic", "503"),
            LLMResult("end_turn", "plain text", None, (), Usage(input=10, output=5), _TARGET),
        ]
    )
    ok, errors = await probe_structured(llm, _authoring_request(), runs=5)
    assert ok == 1
    assert errors == ["InvalidOutput", "InvalidOutput", "ProviderUnavailable", "UnparsedOutput"]
