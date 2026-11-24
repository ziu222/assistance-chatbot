"""Ghép cặp và chấm bằng code cho so sánh shadow (thiết kế 04 §4–§5)."""

from datetime import UTC, datetime, timedelta

from app.modules.shadow.domain.checks import Product, check
from app.modules.shadow.domain.pairing import Msg, pair

T0 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)


def m(seq: int, sender: str, minutes: float, text: str = "x") -> Msg:
    return Msg(seq, sender, text, T0 + timedelta(minutes=minutes))


def test_staff_messages_in_a_row_become_one_reference():
    p = pair(
        3,
        T0,
        [m(4, "human", 1, "Dạ còn ạ"), m(5, "human", 2, "giá 350k"), m(6, "customer", 3)],
        T0 + timedelta(minutes=5),
    )
    assert p.status == "paired" and p.text == "Dạ còn ạ\ngiá 350k"
    assert p.first_reply_at == T0 + timedelta(minutes=1)


def test_system_messages_are_not_staff_replies():
    p = pair(
        3,
        T0,
        [m(4, "system", 0.1, "Ngoài giờ"), m(5, "human", 30, "Dạ có ạ")],
        T0 + timedelta(hours=1),
    )
    assert p.status == "paired" and p.text == "Dạ có ạ"


def test_customer_writing_again_first_supersedes_the_draft():
    assert (
        pair(3, T0, [m(4, "customer", 1), m(5, "human", 2)], T0 + timedelta(hours=1)).status
        == "superseded"
    )
    assert pair(3, T0, [m(4, "customer", 1)], T0 + timedelta(hours=1)).status == "superseded"


def test_waits_while_staff_may_still_be_typing_and_closes_after_the_window():
    early = pair(3, T0, [m(4, "human", 1)], T0 + timedelta(minutes=2))
    assert early.status == "pending"
    settled = pair(3, T0, [m(4, "human", 1)], T0 + timedelta(minutes=4))
    assert settled.status == "paired"
    late = pair(3, T0, [m(4, "human", 1, "a"), m(5, "human", 15, "b")], T0 + timedelta(minutes=16))
    assert late.text == "a"  # tin sau cửa sổ 10 phút không thuộc câu này


def test_unanswered_only_after_a_day():
    assert pair(3, T0, [], T0 + timedelta(hours=2)).status == "pending"
    assert pair(3, T0, [], T0 + timedelta(hours=25)).status == "unanswered"


CATALOG = [Product("AK-02", "Áo khoác gió"), Product("Q-01", "Quần jean nữ")]
KNOWN = {350000, 420000, 25000}


def run(draft: str, ref: str, *, skus=("AK-02",), grounded=True, needs_human=False):
    return check(
        draft_text=draft,
        draft_skus=set(skus),
        grounded=grounded,
        needs_human=needs_human,
        reference=ref,
        known_amounts=KNOWN,
        catalog=CATALOG,
    )


def test_same_price_and_product_needs_the_judge():
    c = run("Dạ áo khoác gió giá 350.000đ ạ", "áo khoác gió 350k nha chị")
    assert (c.amounts, c.products, c.verdict) == ("agree", "match", None)


def test_conflicting_price_is_wrong_unless_the_staff_price_is_unknown():
    assert run("giá 350.000đ", "giá 420.000đ ạ").verdict == "wrong"
    suspect = run("giá 350.000đ", "giá 300.000đ ạ")  # 300k không có trong catalog/tri thức
    assert suspect.reference_suspect and suspect.verdict is None


def test_different_product_and_blocked_draft():
    assert run("Áo khoác gió còn ạ", "Quần jean nữ còn ạ").products == "differ"
    assert run("x", "y", grounded=False).verdict == "not_sendable"


def test_handoff_estimate():
    assert run("Em nhờ nhân viên ạ", "áo khoác gió giá 350k", needs_human=True).handoff == "extra"
    assert run("giá 350.000đ", "Dạ để em kiểm tra lại rồi báo chị nhé").handoff == "missing"
    assert run("Em nhờ nhân viên ạ", "Dạ để em kiểm tra lại", needs_human=True).handoff == "agree"


def test_links_and_missing_amounts():
    c = run("Đặt tại https://shop.vn/a", "link nè https://shop.vn/a ship 25k")
    assert (c.links, c.amounts) == ("match", "draft_missing")
