"""Gọi provider THẬT (tốn vài token). Chạy: `pytest -m live tests/live`. Mặc định bị bỏ qua.

Mỗi provider: chữ thường, structured output, và một vòng tool (gọi tool → trả kết quả → trả lời).
Provider thiếu key thì skip riêng provider đó."""

import pytest
from pydantic import BaseModel

from app.config.settings import get_settings
from app.modules.agent_runtime.llm.factory import build_backends
from app.modules.agent_runtime.llm.port import (
    LLMRequest,
    Target,
    ToolResult,
    ToolSpec,
    Turn,
)
from app.modules.agent_runtime.llm.pricing import cost_micro

pytestmark = pytest.mark.live

TARGETS = [
    Target("anthropic", "claude-sonnet-5", max_tokens=1024, effort="low", timeout_s=60),
    Target("openai", "gpt-4.1-mini", max_tokens=512, timeout_s=60),
]
LOOKUP = ToolSpec(
    "lookup_price",
    "Return the price of a product by SKU.",
    {
        "type": "object",
        "properties": {"sku": {"type": "string"}},
        "required": ["sku"],
        "additionalProperties": False,
    },
)


class Verdict(BaseModel):
    intent: str
    confident: bool


@pytest.fixture(params=TARGETS, ids=lambda t: t.provider)
def live(request):
    backends = build_backends(get_settings())
    t: Target = request.param
    if t.provider not in backends:
        pytest.skip(f"no key for {t.provider}")
    return backends[t.provider], t


def req(text, **kw) -> LLMRequest:
    return LLMRequest("live", ("Answer briefly.",), (Turn("user", text=text),), **kw)


async def test_text_and_usage(live):
    backend, t = live
    r = await backend.complete(t, req("Say the word: pong"), t.timeout_s)
    assert r.stop_reason == "end_turn" and "pong" in (r.text or "").lower()
    assert r.usage.output > 0 and cost_micro(t.provider, t.model, r.usage) > 0


async def test_structured_output(live):
    backend, t = live
    r = await backend.complete(
        t,
        req("Classify: 'how much is the red bag?' (intent=price_query)", output_schema=Verdict),
        t.timeout_s,
    )
    assert isinstance(r.parsed, Verdict) and r.parsed.intent


async def test_tool_round_trip(live):
    backend, t = live
    first = req("What does SKU RB-1 cost? Use the tool.", tools=(LOOKUP,))
    r = await backend.complete(t, first, t.timeout_s)
    assert r.stop_reason == "tool_use" and r.tool_calls[0].arguments["sku"]
    call = r.tool_calls[0]
    followup = LLMRequest(
        "live",
        first.system_blocks,
        (
            *first.turns,
            Turn("assistant", text=r.text, tool_calls=r.tool_calls),
            Turn("tool_results", tool_results=(ToolResult(call.id, "199000 VND"),)),
        ),
        tools=(LOOKUP,),
    )
    done = await backend.complete(t, followup, t.timeout_s)
    assert done.stop_reason == "end_turn" and "199" in (done.text or "")
