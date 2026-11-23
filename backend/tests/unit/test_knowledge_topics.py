"""Chủ đề và độ phủ tri thức (bước 2.13, §13.3)."""

import json
from pathlib import Path

import pytest

from app.modules.knowledge.domain.topics import (
    TOPICS,
    covered,
    effective_topics,
    missing_line,
    suggest_topics,
)

SHOPS = Path(__file__).parents[2] / "evals" / "sim_shop" / "shops"


@pytest.mark.parametrize("shop", sorted(p.name for p in SHOPS.iterdir()))
def test_coverage_matches_what_each_simulated_shop_wrote(shop):
    """12 shop khai báo sẵn mục nào đã viết: đoán từ FAQ/chính sách phải ra đúng như vậy."""
    folder = SHOPS / shop
    declared = set(
        json.loads((folder / "shop.json").read_text(encoding="utf-8"))["knowledge"]["sections"]
    )
    texts = []
    if (folder / "faq.json").exists():
        texts += [
            f"{f['question']} {f['answer']}"
            for f in json.loads((folder / "faq.json").read_text(encoding="utf-8"))
        ]
    if (folder / "policy.md").exists():
        # Chính sách vào Atlas như MỘT tài liệu, đúng như lúc chạy thật.
        texts.append((folder / "policy.md").read_text(encoding="utf-8"))
    assert covered(suggest_topics(t) for t in texts) == declared


def test_keywords_without_accents_and_owner_override():
    assert suggest_topics("ship noi thanh bao nhieu, co cod ko") == ["shipping", "payment"]
    assert suggest_topics("Đổi trả trong 7 ngày; lỗi do shop thì shop chịu phí ship hai chiều") == [
        "returns"
    ]
    assert effective_topics(None, "bảo hành 12 tháng") == ["warranty"]
    assert effective_topics([], "bảo hành 12 tháng") == []  # chủ shop bỏ chọn: tôn trọng
    assert effective_topics(["promo", "khong-co"], "") == ["promo"]


def test_missing_line_is_stable_and_empty_when_complete():
    line = missing_line({"shipping", "returns"})
    assert line.startswith("Shop chưa có thông tin về: giờ làm việc, thanh toán và COD, bảo hành")
    assert missing_line({"returns", "shipping"}) == line  # thứ tự cố định ⇒ cache được
    assert missing_line(set(TOPICS)) == ""
