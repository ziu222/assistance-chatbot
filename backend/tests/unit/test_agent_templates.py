import pytest
from pydantic import ValidationError

from app.modules.agent_management.infrastructure.templates import (
    AgentTemplate,
    get_template,
    load_templates,
)


def test_shipped_templates_are_valid():
    templates = load_templates()
    assert ("faq_sales_quote", 1) in templates
    t = get_template("faq_sales_quote", 1)
    assert t.default_mode == "shadow"
    assert set(t.handoff_intents) <= set(t.intents)
    assert t.tools == ["search_products", "get_product"]
    assert "{shop_name}" in t.instructions


def test_unknown_template_raises_key_error():
    with pytest.raises(KeyError):
        get_template("nope", 1)


def base(**over) -> dict:
    data = {
        "key": "test_agent",
        "version": 1,
        "default_mode": "shadow",
        "intents": {"faq": "Hỏi đáp", "complaint": "Phàn nàn"},
        "handoff_intents": ["complaint"],
        "tools": ["search_products"],
        "routes": {"classify": "classify", "draft": "draft"},
        "budget": {
            "max_model_calls": 3,
            "max_tool_calls": 5,
            "wall_clock_s": 60,
            "max_cost_micro": 50000,
        },
        "instructions": "Bạn là trợ lý của {shop_name}.",
    }
    return data | over


@pytest.mark.parametrize(
    "over",
    [
        {"handoff_intents": ["refund"]},  # không có trong intents
        {"tools": ["issue_refund"]},  # tool có side-effect / không tồn tại
        {"default_mode": "autopilot"},  # bước 2 chỉ manual/shadow
        {"key": "Bad Key"},
        {"version": 0},
        {"instructions": "thiếu placeholder"},
        {
            "budget": {
                "max_model_calls": 0,
                "max_tool_calls": 5,
                "wall_clock_s": 60,
                "max_cost_micro": 1,
            }
        },
        {"extra_field": 1},
        {"routes": {"classify": "classify"}},  # thiếu route draft
        {"routes": {"classify": "Bad Name", "draft": "draft"}},
    ],
)
def test_invalid_templates_are_rejected(over):
    with pytest.raises(ValidationError):
        AgentTemplate.model_validate(base(**over))


def test_base_is_valid():
    assert AgentTemplate.model_validate(base()).key == "test_agent"
