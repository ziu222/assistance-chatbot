"""Kiểm tra giữ sự thật của trợ lý viết (bước 2.11, thiết kế §12.8)."""

from app.modules.authoring.domain.facts import check, facts

ORIGINAL = (
    "Dạ {shop_name} chào bạn! Shop đổi trả trong 7 ngày, phí ship nội thành 30.000đ. "
    "Mở cửa 8h–21h30, hotline 0901 234 567."
)


def test_rewording_that_keeps_every_fact_is_allowed():
    good = (
        "{shop_name} xin chào bạn ạ! Bên em hỗ trợ đổi trả trong vòng 7 ngày, ship nội thành "
        "30k. Shop mở từ 8h tới 21h30, gọi 0901.234.567 nếu cần nhé."
    )
    result = check(ORIGINAL, good)
    assert result.ok, result.as_json()
    assert result.new_promises == []


def test_changed_return_window_is_blocked():
    bad = ORIGINAL.replace("7 ngày", "30 ngày")
    result = check(ORIGINAL, bad)
    assert not result.ok
    assert result.missing == {"durations": ["7 ngay"]}
    assert result.added == {"durations": ["30 ngay"]}


def test_changed_price_phone_and_dropped_variable_are_blocked():
    bad = (
        "Chào bạn! Shop đổi trả trong 7 ngày, phí ship nội thành 25.000đ. "
        "Mở cửa 8h–21h30, hotline 0901 234 568."
    )
    result = check(ORIGINAL, bad)
    assert set(result.missing) == {"amounts", "phones", "variables"}
    assert result.added["amounts"] == ["25000"]


def test_new_promise_is_a_warning_not_a_block():
    result = check("Shop giao hàng toàn quốc.", "Shop giao hàng toàn quốc, freeship mọi đơn!")
    assert result.ok and result.new_promises == ["freeship"]
    assert check("Freeship đơn từ 500k.", "Đơn từ 500k được freeship.").new_promises == []


def test_facts_normalise_formats():
    f = facts("Giao trong 2 tiếng, mở 8:00, ship 30k, xem www.shop.vn.")
    assert f["durations"] == {"2 gio"}
    assert f["times"] == {"8:00"}
    assert f["amounts"] == {"30000"}
    assert f["links"] == {"www.shop.vn"}
