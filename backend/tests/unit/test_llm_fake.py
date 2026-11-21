import dataclasses

import pytest
from pydantic import BaseModel

from app.modules.agent_runtime.llm.fake import FakeBackend, FakeLLM, text_result, tool_call_result
from app.modules.agent_runtime.llm.port import (
    BadRequest,
    LLMRequest,
    ProviderUnavailable,
    Target,
    ToolCall,
    ToolResult,
    Turn,
    Usage,
)
from app.modules.agent_runtime.tools import TOOL_SPECS, TOOLS


def request(**over) -> LLMRequest:
    base = dict(
        route="draft",
        system_blocks=("Bạn là trợ lý.",),
        turns=(Turn(role="user", text="áo size M còn không?"),),
    )
    return LLMRequest(**(base | over))


async def test_fake_llm_returns_script_in_order_and_records_requests():
    llm = FakeLLM([text_result("một"), text_result("hai")])
    r1, r2 = request(), request(route="classify")

    assert (await llm.complete(r1)).text == "một"
    assert (await llm.complete(r2)).text == "hai"
    assert llm.requests == [r1, r2]  # runtime test kiểm tra đúng cái đã gửi đi


async def test_fake_llm_raises_scripted_errors_and_when_script_is_empty():
    llm = FakeLLM([ProviderUnavailable("anthropic", "timeout")])
    with pytest.raises(ProviderUnavailable):
        await llm.complete(request())
    with pytest.raises(AssertionError, match="script"):
        await llm.complete(request())  # quên viết kịch bản: lỗi rõ ràng, không treo


async def test_fake_llm_callable_step_sees_the_request():
    llm = FakeLLM([lambda req: text_result(req.turns[-1].text.upper())])
    assert (await llm.complete(request())).text == "ÁO SIZE M CÒN KHÔNG?"


async def test_fake_backend_records_target_and_timeout():
    backend = FakeBackend("anthropic", [text_result("ok")])
    target = Target("anthropic", "claude-sonnet-5", max_tokens=512, effort="low")
    result = await backend.complete(target, request(), timeout=12.5)

    assert backend.calls[0].target == target and backend.calls[0].timeout == 12.5
    assert result.served_by == target  # backend ghi đúng đích đã phục vụ


def test_result_helpers_build_valid_results():
    call = ToolCall("t1", "search_products", {"query": "áo"})
    r = tool_call_result(call)
    assert (r.stop_reason, r.tool_calls) == ("tool_use", (call,))
    assert text_result("x", usage=Usage(input=5, output=2)).usage.output == 2


def test_types_are_immutable():
    r = request()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.route = "classify"
    turn = Turn(role="tool_results", tool_results=(ToolResult("t1", "[]"),))
    assert turn.tool_results[0].is_error is False


def test_tools_are_derived_from_strict_specs():
    assert [t.name for t in TOOLS] == [s["name"] for s in TOOL_SPECS]
    assert all(t.input_schema["additionalProperties"] is False for t in TOOLS)


def test_bad_request_is_not_a_provider_problem():
    assert not issubclass(BadRequest, ProviderUnavailable)


class Schema(BaseModel):
    reply: str


async def test_structured_result_can_carry_parsed_model():
    r = dataclasses.replace(text_result('{"reply": "x"}'), parsed=Schema(reply="x"))
    assert r.parsed.reply == "x"
