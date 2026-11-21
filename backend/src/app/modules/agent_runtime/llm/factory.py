"""Dựng FailoverLLM từ cấu hình. Sai cấu hình ⇒ lỗi ngay lúc khởi động (fail fast)."""

from collections.abc import Collection

from app.config.settings import Settings
from app.modules.agent_runtime.llm.anthropic import AnthropicBackend
from app.modules.agent_runtime.llm.failover import FailoverLLM
from app.modules.agent_runtime.llm.openai import OpenAIBackend
from app.modules.agent_runtime.llm.port import Backend
from app.modules.agent_runtime.llm.routes import (
    Routes,
    load_routes,
    restrict_to_available,
    validate_prices,
    validate_route_names,
)


def build_backends(s: Settings) -> dict[str, Backend]:
    off = {p.strip() for p in s.llm_disabled_providers.split(",") if p.strip()}
    backends: dict[str, Backend] = {}
    if s.claude_api_key and "anthropic" not in off:
        backends["anthropic"] = AnthropicBackend(s.claude_api_key, base_url=s.llm_base_url or None)
    # ponytail: llm_base_url chỉ áp cho Anthropic (gateway thử nghiệm); OpenAI gọi thẳng.
    if s.openai_api_key and "openai" not in off:
        backends["openai"] = OpenAIBackend(s.openai_api_key)
    return backends


def build_llm(
    s: Settings,
    used_routes: dict[str, Collection[str]] | None = None,
    backends: dict[str, Backend] | None = None,
) -> tuple[FailoverLLM, Routes]:
    """`used_routes`: route mà từng template cần; thiếu route nào ⇒ lỗi ngay."""
    backends = build_backends(s) if backends is None else backends
    routes = load_routes()
    validate_route_names(used_routes or {}, routes)
    routes = restrict_to_available(routes, backends.keys())
    validate_prices(routes)
    return FailoverLLM(routes, backends), routes
