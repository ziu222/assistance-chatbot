"""Dựng LLM từ cấu hình: thiếu key/route/giá ⇒ lỗi ngay, không đợi khách đầu tiên."""

import pytest

from app.config.settings import Settings
from app.modules.agent_runtime.llm.factory import build_backends, build_llm
from app.modules.agent_runtime.llm.pricing import UnknownPrice
from app.modules.agent_runtime.llm.routes import NoTargetAvailable


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_backends_follow_the_configured_keys():
    assert set(build_backends(settings(claude_api_key="a", openai_api_key="o"))) == {
        "anthropic",
        "openai",
    }
    assert set(build_backends(settings(openai_api_key="o"))) == {"openai"}
    assert build_backends(settings()) == {}


def test_disabled_provider_is_dropped_even_with_a_key():
    s = settings(claude_api_key="a", openai_api_key="o", llm_disabled_providers=" anthropic ,")
    assert set(build_backends(s)) == {"openai"}


def test_build_llm_keeps_only_available_targets():
    _, routes = build_llm(settings(openai_api_key="o"))
    assert {t.provider for ts in routes.values() for t in ts} == {"openai"}
    _, both = build_llm(settings(claude_api_key="a", openai_api_key="o"))
    assert [t.provider for t in both["draft"]] == ["anthropic", "openai"]


def test_no_keys_fails_fast():
    with pytest.raises(NoTargetAvailable):
        build_llm(settings())


def test_template_route_missing_from_routes_fails_fast():
    with pytest.raises(NoTargetAvailable, match="faq needs route 'nonexistent'"):
        build_llm(settings(openai_api_key="o"), {"faq": ["draft", "nonexistent"]})


def test_build_llm_rejects_a_route_without_a_price(monkeypatch):
    from app.modules.agent_runtime.llm import pricing

    monkeypatch.delitem(pricing.PRICES, ("openai", "gpt-4.1"))
    with pytest.raises(UnknownPrice):
        build_llm(settings(openai_api_key="o"))
