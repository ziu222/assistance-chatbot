from app.modules.outbound.domain.autopilot import (
    ReadinessSnapshot,
    can_enable_intent,
    extract_config_key,
)

_CFG = {"template": "faq_sales_quote", "template_version": 5, "prompt_version": "2026-11-23.1"}


def test_extract_config_key_drops_model_and_knowledge_hash():
    raw = _CFG | {"model": "claude-sonnet-5", "knowledge_hash": "abc123"}
    assert extract_config_key(raw) == _CFG
    assert extract_config_key({"template": "x"}) is None


def test_forbidden_and_opened_intents():
    ready = ReadinessSnapshot(True, (), _CFG)
    for bad in ("complaint", "human_request", "discount_request", "order_intent", "other"):
        res = can_enable_intent(bad, current_config_key=_CFG, blind=ready, shown=ready)
        assert not res.allowed

    # A6 mở `price_quote` khi đủ mức sẵn sàng (đi kèm kiểm `catalog_stale` ở cổng gửi).
    price = can_enable_intent("price_quote", current_config_key=_CFG, blind=ready, shown=ready)
    assert price.allowed and price.source == "blind"

    handoff = can_enable_intent(
        "faq",
        handoff_intents=frozenset({"faq"}),
        current_config_key=_CFG,
        blind=ready,
        shown=ready,
    )
    assert not handoff.allowed


def test_first_enable_requires_blind_ready_matching_current_config():
    ready = ReadinessSnapshot(True, (), _CFG)
    not_ready = ReadinessSnapshot(False, ("chưa đủ mẫu (12/30 cặp được chấm)",), _CFG)
    stale_cfg = ReadinessSnapshot(True, (), _CFG | {"prompt_version": "old"})

    ok = can_enable_intent("faq", current_config_key=_CFG, blind=ready, shown=None)
    assert ok.allowed and ok.source == "blind" and ok.config_key == _CFG

    # Chỉ sẵn sàng trên nguồn shown mà chưa từng bật trước đó ⇒ từ chối
    only_shown = can_enable_intent(
        "faq", current_config_key=_CFG, blind=not_ready, shown=ready, previously_enabled=False
    )
    assert not only_shown.allowed
    assert any("nguồn mù" in r for r in only_shown.reasons)

    # Đã từng bật và bị hạ cấp ⇒ cho phép bật lại bằng nguồn shown
    re_enable = can_enable_intent(
        "faq", current_config_key=_CFG, blind=not_ready, shown=ready, previously_enabled=True
    )
    assert re_enable.allowed and re_enable.source == "shown"

    # Số đo thuộc cấu hình cũ ⇒ từ chối
    old = can_enable_intent("faq", current_config_key=_CFG, blind=stale_cfg, shown=None)
    assert not old.allowed
    assert any("cấu hình AI cũ" in r for r in old.reasons)
