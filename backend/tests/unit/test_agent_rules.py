"""Rule xác định và kiểm tra nháp: không DB, không LLM."""

import pytest

from app.modules.agent_runtime.rules import GREETING_REPLY, THANKS_REPLY, apply_rules
from app.modules.agent_runtime.schemas import DraftOut, Source
from app.modules.agent_runtime.validation import extract_amounts, validate_draft


@pytest.mark.parametrize(
    "texts",
    [["cho em gặp nhân viên"], ["Shop lừa đảo!"], ["alo", "muốn khiếu nại đơn hôm qua"]],
)
def test_handoff_rule(texts):
    out = apply_rules(texts)
    assert out is not None and out.kind == "handoff"


@pytest.mark.parametrize(
    ("texts", "reply"),
    [
        (["alo shop"], GREETING_REPLY),
        (["Xin chào!"], GREETING_REPLY),
        (["cảm ơn shop"], THANKS_REPLY),
        (["alo", "cảm ơn nha"], THANKS_REPLY),
    ],
)
def test_canned_rule(texts, reply):
    out = apply_rules(texts)
    assert out is not None and out.kind == "canned" and out.reply == reply


@pytest.mark.parametrize(
    "texts", [["áo khoác gió giá bao nhiêu"], ["alo shop còn size M không"], [""], []]
)
def test_other_text_goes_on_to_the_model(texts):
    assert apply_rules(texts) is None


@pytest.mark.parametrize(
    ("text", "amounts"),
    [
        ("giá 350.000đ", {350_000}),
        ("350,000 VNĐ", {350_000}),
        ("còn 350k thôi ạ", {350_000}),
        ("khoảng 1,2 triệu", {1_200_000}),
        ("2 tr", {2_000_000}),
        ("giá 1.000", {1_000}),  # có dấu nghìn: tính là tiền dù thiếu đơn vị
        ("size 38, đổi trong 7 ngày, nặng 2 kg", set()),
        ("ship 30.000đ, áo 350k", {30_000, 350_000}),
    ],
)
def test_extract_amounts(text, amounts):
    assert extract_amounts(text) == amounts


def draft(reply, sources=()):
    return DraftOut(
        reply=reply,
        sources=[Source(type=t, id=i) for t, i in sources],
        needs_human=False,
        reason="",
    )


VALID = {("faq", "f1"), ("product", "p1")}


def check(reply, sources=(), amounts=frozenset({350_000})):
    return validate_draft(draft(reply, sources), valid_sources=VALID, known_amounts=set(amounts))


def test_valid_draft_has_no_problems():
    assert check("Áo giá 350.000đ ạ", [("product", "p1")]) == []


def test_unknown_source_is_flagged():
    assert check("ok", [("product", "p2")]) == ["source_not_provided"]
    assert check("ok", [("faq", "p1")]) == ["source_not_provided"]  # đúng id nhưng sai loại


@pytest.mark.parametrize("reply", ["xem https://x.vn", "vào www.x.vn", "<b>giá</b>", "a <script>"])
def test_url_and_html_are_flagged(reply):
    assert "url_or_html" in check(reply)


def test_amount_must_match_known_prices():
    assert check("giá 1.000đ") == ["amount_mismatch"]
    assert check("giá 350k") == []


def test_empty_reply_is_flagged():
    assert "length" in check("   ")
