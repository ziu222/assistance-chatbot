"""Kiểm thử hàm thuần `send_decision`: mặc định chặn, mọi lý do chặn, lượt nhiều ý định."""

from dataclasses import replace

import pytest

from app.modules.outbound.domain.gate import AiGateInput, send_decision

_CFG = {"template_key": "faq_sales_quote", "template_version": 5, "prompt_version": "v1"}


def _passing(**overrides) -> AiGateInput:
    base = AiGateInput(
        ai_enabled=True,
        auto_send=True,
        intents=("faq",),
        enabled_intents=frozenset({"faq", "greeting"}),
        handoff_intents=frozenset({"complaint", "human_request"}),
        run_config_key=_CFG,
        enabled_config_keys={"faq": _CFG, "greeting": _CFG},
        served_model_ok=True,
        grounded=True,
        validation_problems=(),
        needs_human=False,
        order_active=False,
        product_mismatch=False,
        handling="bot",
        conversation_status="open",
        based_on_sequence=5,
        latest_customer_sequence=5,
        human_replied_after=False,
        bot_streak=0,
        max_bot_streak=3,
        provider="webchat",
        channel_allowed=True,
        catalog_stale=False,
    )
    return replace(base, **overrides)


def test_default_input_is_blocked_fail_closed():
    assert send_decision(AiGateInput()) == "kill_switch"


def test_passing_input_allows_send():
    assert send_decision(_passing()) is None
    assert send_decision(_passing(intents=("greeting", "faq"))) is None
    # Chưa có adapter giao tin cho Messenger/Zalo OA ⇒ chặn dù đang trong cửa sổ.
    assert send_decision(_passing(provider="messenger", channel_allowed=True)) == "channel"
    assert send_decision(_passing(provider="zalo_oa", channel_allowed=True)) == "channel"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"ai_enabled": False}, "kill_switch"),
        ({"auto_send": False}, "kill_switch"),
        ({"intents": ()}, "intent_not_enabled"),
        ({"intents": ("product_info",)}, "intent_not_enabled"),
        ({"intents": ("faq", "discount_request")}, "intent_not_enabled"),
        ({"intents": ("faq", "order_intent")}, "intent_not_enabled"),
        ({"intents": ("faq", "other")}, "intent_not_enabled"),
        (
            {
                "intents": ("faq",),
                "enabled_intents": frozenset({"faq"}),
                "handoff_intents": frozenset({"faq"}),
            },
            "intent_not_enabled",
        ),
        ({"run_config_key": None}, "config_changed"),
        ({"run_config_key": _CFG | {"prompt_version": "v2"}}, "config_changed"),
        ({"enabled_config_keys": {}}, "config_changed"),
        ({"served_model_ok": False}, "model_not_primary"),
        ({"grounded": False}, "not_grounded"),
        ({"validation_problems": ("amount_mismatch",)}, "not_grounded"),
        ({"needs_human": True}, "needs_human"),
        ({"order_active": True}, "order_flow"),
        ({"product_mismatch": True}, "product_mismatch"),
        ({"handling": "human"}, "handling"),
        ({"handling": "waiting_human"}, "handling"),
        ({"conversation_status": "closed"}, "handling"),
        ({"based_on_sequence": 0}, "stale"),
        ({"latest_customer_sequence": 6}, "stale"),
        ({"human_replied_after": True}, "stale"),
        ({"bot_streak": 3}, "bot_streak"),
        ({"bot_streak": 4}, "bot_streak"),
        ({"provider": "shopee"}, "channel"),
        ({"channel_allowed": False}, "channel"),
        ({"catalog_stale": True}, "catalog_stale"),
    ],
)
def test_every_block_reason(overrides, expected):
    assert send_decision(_passing(**overrides)) == expected


def test_block_priority_order_cheap_checks_first():
    # kill_switch thắng mọi lý do phía sau
    assert (
        send_decision(
            _passing(ai_enabled=False, grounded=False, needs_human=True, catalog_stale=True)
        )
        == "kill_switch"
    )
    # intent_not_enabled thắng config_changed và not_grounded
    assert (
        send_decision(_passing(intents=("other",), run_config_key=None, grounded=False))
        == "intent_not_enabled"
    )
    # config_changed thắng model_not_primary
    assert (
        send_decision(
            _passing(run_config_key=_CFG | {"prompt_version": "v2"}, served_model_ok=False)
        )
        == "config_changed"
    )
