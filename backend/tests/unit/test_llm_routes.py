import pytest
from pydantic import ValidationError

from app.modules.agent_runtime.llm.port import Target
from app.modules.agent_runtime.llm.pricing import UnknownPrice
from app.modules.agent_runtime.llm.routes import (
    NoTargetAvailable,
    load_routes,
    parse_routes,
    restrict_to_available,
    validate_prices,
)


def good() -> dict:
    return {
        "routes": {
            "classify": {
                "targets": [
                    {
                        "provider": "anthropic",
                        "model": "claude-sonnet-5",
                        "effort": "low",
                        "max_tokens": 1024,
                    },
                    {"provider": "openai", "model": "gpt-4.1-mini", "max_tokens": 512},
                ]
            },
            "draft": {
                "targets": [
                    {
                        "provider": "anthropic",
                        "model": "claude-sonnet-5",
                        "effort": "medium",
                        "max_tokens": 4096,
                    },
                    {"provider": "openai", "model": "gpt-4.1", "max_tokens": 2048},
                ]
            },
        }
    }


def test_shipped_routes_are_valid_priced_and_have_a_backup():
    routes = load_routes()
    validate_prices(routes)  # model nào vào route cũng phải có giá
    for name in ("classify", "draft"):
        providers = [t.provider for t in routes[name]]
        assert providers[0] == "anthropic"  # đích chính (ADR-0015)
        assert "openai" in providers[1:]  # luôn có dự phòng khác nhà cung cấp
    assert routes["draft"][0] == Target(
        "anthropic", "claude-sonnet-5", max_tokens=4096, effort="medium", timeout_s=30.0
    )


def test_good_routes_parse_into_ordered_targets():
    routes = parse_routes(good())
    assert [t.label for t in routes["draft"]] == ["anthropic:claude-sonnet-5", "openai:gpt-4.1"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["routes"]["draft"]["targets"][1].update(
            effort="low"
        ),  # effort vô nghĩa với openai
        lambda d: d["routes"]["draft"]["targets"][0].update(provider="gemini"),
        lambda d: d["routes"]["draft"].update(targets=[]),
        lambda d: d["routes"]["draft"]["targets"][0].update(max_tokens=10),
        lambda d: d["routes"]["draft"]["targets"][0].update(max_tokens=10**6),
        lambda d: d["routes"]["draft"]["targets"][0].update(timeout_s=0),
        lambda d: d["routes"]["draft"]["targets"][0].update(model=""),
        lambda d: d["routes"]["draft"]["targets"][0].update(unknown_field=1),
        lambda d: d["routes"].pop("draft"),  # runtime cần cả classify lẫn draft
        lambda d: d["routes"].update({"Bad Name": d["routes"]["draft"]}),
        lambda d: d.update(extra=1),
    ],
)
def test_invalid_routes_are_rejected(mutate):
    data = good()
    mutate(data)
    with pytest.raises(ValidationError):
        parse_routes(data)


def test_restrict_drops_unavailable_providers_but_keeps_order():
    routes = restrict_to_available(parse_routes(good()), {"openai"})
    assert [t.label for t in routes["draft"]] == ["openai:gpt-4.1"]
    both = restrict_to_available(parse_routes(good()), {"anthropic", "openai"})
    assert len(both["draft"]) == 2


def test_restrict_fails_fast_when_a_route_would_be_empty():
    with pytest.raises(NoTargetAvailable, match="draft"):
        restrict_to_available(parse_routes(good()), set())


def test_validate_prices_names_the_missing_model():
    data = good()
    data["routes"]["draft"]["targets"][1]["model"] = "gpt-9-imaginary"
    with pytest.raises(UnknownPrice, match="gpt-9-imaginary"):
        validate_prices(parse_routes(data))
