"""Contract test CHUNG cho mọi adapter provider (ADR-0015): cùng bộ ca, cùng kết quả chuẩn hóa.

Thêm provider mới = thêm một harness vào `llm_harness.HARNESSES`; adapter phải qua đúng bộ này.
"""

import dataclasses

import httpx2 as httpx
import pytest
from llm_harness import HARNESSES, AnthropicHarness, body_of, flat_text
from pydantic import BaseModel

from app.modules.agent_runtime.llm.port import (
    BadRequest,
    InvalidOutput,
    LLMRequest,
    ProviderUnavailable,
    ToolCall,
    ToolResult,
    Turn,
    Usage,
)
from app.modules.agent_runtime.tools import TOOLS

TIMEOUT = 5.0


class Draft(BaseModel):
    reply: str
    needs_human: bool


def request(**over) -> LLMRequest:
    base = dict(
        route="draft",
        system_blocks=("FAQ-7: đổi trả trong 7 ngày nếu còn tem", "DOC-3: giờ mở cửa 8h-21h"),
        turns=(Turn("user", "áo khoác gió size M còn không?"),),
    )
    return LLMRequest(**(base | over))


class Recorder:
    """Handler ghi lại mọi request và trả phản hồi định sẵn."""

    def __init__(self, response):
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


harness = pytest.mark.parametrize("h", HARNESSES, ids=lambda h: h.name)


async def run(h, response, req=None):
    rec = Recorder(response)
    result = await h.backend(rec).complete(h.target, req or request(), TIMEOUT)
    return result, rec


@harness
async def test_text_roundtrip_with_normalized_usage(h):
    result, _ = await run(h, h.text("Dạ còn ạ", input=100, cache_read=900, output=20))
    assert (result.stop_reason, result.text, result.tool_calls) == ("end_turn", "Dạ còn ạ", ())
    # chuẩn hóa: `input` là phần KHÔNG nằm trong cache, dù provider báo tổng hay riêng
    assert result.usage == Usage(input=100, cache_read=900, cache_write=0, output=20)
    assert result.served_by == h.target


@harness
async def test_tool_call_is_parsed_into_neutral_form(h):
    args = {"query": "áo khoác gió", "limit": 3}
    result, _ = await run(h, h.tool_call("call_1", "search_products", args))
    assert result.stop_reason == "tool_use"
    assert result.tool_calls == (ToolCall("call_1", "search_products", args),)


@harness
async def test_all_knowledge_reaches_the_wire(h):
    """ADR-0030: failover chỉ đổi adapter, nên adapter nào cũng không được làm rơi tri thức."""
    turns = (
        Turn("user", "cho em xem áo khoác gió"),
        Turn(
            "assistant",
            text="Em tra giúp ạ",
            tool_calls=(ToolCall("c1", "search_products", {"query": "áo khoác gió"}),),
        ),
        Turn(
            "tool_results", tool_results=(ToolResult("c1", '[{"sku":"AK-02","price_vnd":350000}]'),)
        ),
        Turn("user", "size M còn không?"),
    )
    _, rec = await run(h, h.text("ok"), request(turns=turns, tools=tuple(TOOLS)))
    sent = flat_text(rec.requests[0])
    for must_have in (
        "FAQ-7: đổi trả trong 7 ngày nếu còn tem",
        "DOC-3: giờ mở cửa 8h-21h",
        "cho em xem áo khoác gió",
        "Em tra giúp ạ",
        "search_products",
        "AK-02",
        "350000",
        "size M còn không?",
    ):
        assert must_have in sent, must_have


@harness
async def test_tools_are_sent_strict_without_tenant(h):
    _, rec = await run(h, h.text("ok"), request(tools=tuple(TOOLS)))
    tools = h.wire_tools(rec.requests[0])
    assert [name for name, _, _ in tools] == ["search_products", "get_product"]
    for _, strict, schema in tools:
        assert strict is True and schema["additionalProperties"] is False
        assert "tenant_id" not in schema["properties"]


@harness
async def test_structured_output_is_parsed_and_validated(h):
    req = request(output_schema=Draft)
    result, rec = await run(h, h.text('{"reply": "Dạ còn ạ", "needs_human": false}'), req)
    assert result.parsed == Draft(reply="Dạ còn ạ", needs_human=False)
    assert "needs_human" in flat_text(rec.requests[0])  # schema thực sự được gửi đi


@harness
@pytest.mark.parametrize("bad", ["không phải json", '{"reply": 5}', '{"reply": "x"}'])
async def test_structured_output_that_breaks_the_schema_is_invalid_output(h, bad):
    with pytest.raises(InvalidOutput):
        await run(h, h.text(bad), request(output_schema=Draft))


@harness
async def test_refusal_is_a_result_not_an_error(h):
    result, _ = await run(h, h.refusal())
    assert result.stop_reason == "refusal" and result.tool_calls == ()


@harness
async def test_truncated_output_reports_max_tokens_without_parsing(h):
    result, _ = await run(
        h, h.text('{"reply": "cụt', stop="max_tokens"), request(output_schema=Draft)
    )
    assert result.stop_reason == "max_tokens" and result.parsed is None


@harness
@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (429, "rate_limited"),
        (500, "server_error"),
        (502, "server_error"),
        (503, "overloaded"),
        (529, "overloaded"),
        (401, "auth"),
        (403, "auth"),
        (402, "quota"),
        (404, "config"),
        (408, "timeout"),
    ],
)
async def test_http_errors_map_to_provider_trouble(h, status, reason):
    with pytest.raises(ProviderUnavailable) as e:
        await run(h, h.error(status))
    assert e.value.reason == reason and e.value.provider == h.name


@harness
@pytest.mark.parametrize("status", [400, 413, 422])
async def test_request_errors_are_not_provider_trouble(h, status):
    with pytest.raises(BadRequest):
        await run(h, h.error(status))


@harness
@pytest.mark.parametrize(
    ("exc", "reason"),
    [(httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectError("down"), "connection")],
)
async def test_transport_errors_map_to_provider_trouble(h, exc, reason):
    with pytest.raises(ProviderUnavailable) as e:
        await run(h, exc)
    assert e.value.reason == reason


@harness
async def test_sdk_never_retries_on_its_own(h):
    rec = Recorder(h.error(500))
    with pytest.raises(ProviderUnavailable):
        await h.backend(rec).complete(h.target, request(), TIMEOUT)
    assert len(rec.requests) == 1  # retry/failover chỉ do FailoverLLM quyết định


@harness
async def test_errors_never_leak_keys_or_prompts(h):
    with pytest.raises(ProviderUnavailable) as e:
        await run(h, h.error(500, message="secret-prompt-echo test-key"))
    text = f"{e.value!r} {e.value}"
    assert "test-key" not in text and "secret-prompt-echo" not in text
    assert e.value.__cause__ is None and e.value.__suppress_context__


@harness
async def test_request_object_is_never_mutated(h):
    req = request(tools=tuple(TOOLS))
    snapshot = dataclasses.replace(req)
    await run(h, h.text("ok"), req)
    assert req == snapshot


# ---------- riêng Anthropic ----------

anthropic_only = pytest.mark.parametrize("h", [AnthropicHarness()], ids=["anthropic"])


@anthropic_only
async def test_cache_breakpoint_is_on_the_last_system_block_only(h):
    _, rec = await run(h, h.text("ok"))
    system = body_of(rec.requests[0])["system"]
    assert [b.get("cache_control") for b in system] == [None, {"type": "ephemeral"}]


@anthropic_only
async def test_effort_goes_in_output_config_and_nothing_forbidden_is_sent(h):
    _, rec = await run(h, h.text("ok"))
    body = body_of(rec.requests[0])
    assert body["output_config"] == {"effort": "medium"}
    assert body["max_tokens"] == 1024 and body["model"] == "claude-sonnet-5"
    for forbidden in ("thinking", "temperature", "top_p", "top_k", "tool_choice"):
        assert forbidden not in body


@anthropic_only
async def test_structured_output_and_effort_share_output_config(h):
    _, rec = await run(
        h, h.text('{"reply": "a", "needs_human": false}'), request(output_schema=Draft)
    )
    config = body_of(rec.requests[0])["output_config"]
    assert config["effort"] == "medium" and config["format"]["type"] == "json_schema"
    assert config["format"]["schema"]["additionalProperties"] is False


@anthropic_only
async def test_cache_write_tokens_are_reported(h):
    result, _ = await run(h, h.text("ok", input=10, cache_write=500))
    assert result.usage == Usage(input=10, cache_read=0, cache_write=500, output=5)


@anthropic_only
async def test_tool_results_become_a_user_turn_with_error_flag(h):
    turns = (
        Turn("user", "x"),
        Turn("assistant", tool_calls=(ToolCall("c1", "get_product", {"product_id": "p"}),)),
        Turn("tool_results", tool_results=(ToolResult("c1", "not_found", is_error=True),)),
    )
    _, rec = await run(h, h.text("ok"), request(turns=turns))
    last = body_of(rec.requests[0])["messages"][-1]
    assert last["role"] == "user"
    assert last["content"] == [
        {"type": "tool_result", "tool_use_id": "c1", "content": "not_found", "is_error": True}
    ]
