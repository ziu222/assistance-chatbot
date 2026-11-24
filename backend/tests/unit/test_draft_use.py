"""Nhân viên dùng, sửa hay bỏ nháp (thiết kế 05 §4.2)."""

from app.modules.learning.domain.draft_use import compare

DRAFT = "Dạ áo khoác gió giá 350.000đ ạ. Anh/chị lấy size nào ạ?"


def test_same_text_after_whitespace_is_as_is():
    u = compare(DRAFT, "Dạ áo khoác gió giá  350.000đ ạ.\nAnh/chị lấy size nào ạ?", used=True)
    assert (u.action, u.edit_ratio, u.minor) == ("as_is", 0.0, True)


def test_small_wording_change_is_a_minor_edit():
    u = compare(DRAFT, "Dạ áo khoác gió giá 350.000đ ạ. Chị lấy size nào ạ?", used=True)
    assert u.action == "edited" and 0 < u.edit_ratio < 0.2 and u.minor


def test_price_change_is_never_minor():
    u = compare(DRAFT, "Dạ áo khoác gió giá 320.000đ ạ. Anh/chị lấy size nào ạ?", used=True)
    assert (u.amounts_added, u.amounts_removed, u.minor) == ([320000], [350000], False)


def test_added_information_and_links():
    u = compare(DRAFT, DRAFT + " Đặt tại https://shop.vn/a nhé. Ship nội thành 25k.", used=True)
    assert u.sentences_added >= 1 and u.links_changed and u.amounts_added == [25000]


def test_discard():
    assert compare(DRAFT, None, used=False).action == "discarded"
    assert compare(DRAFT, "Dạ chị đợi em xíu", used=False).action == "discarded"
