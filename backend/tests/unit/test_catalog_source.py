"""Chuẩn hóa catalog từ nhiều nguồn (bước 2.13, §13.2) trên mẫu thô kiểu sàn của bộ giả lập."""

import json
from pathlib import Path

import pytest

from app.modules.knowledge.domain.catalog_source import (
    clean_for_search,
    from_market_listing,
    split_variant,
)

SHOPS = Path(__file__).parents[2] / "evals" / "sim_shop" / "shops"
LISTINGS = sorted(SHOPS.glob("*/raw_listing.json"))


def test_search_text_drops_promo_hashtag_and_emoji_but_keeps_words():
    title = "[Mã OT98 giảm 30K] Áo thun 🔥HOT🔥 cổ tròn ✅ #ootd #aothun"
    assert clean_for_search(title) == "ao thun hot co tron"


def test_split_variant():
    assert split_variant("đen,M") == {"size": "M", "color": "đen"}
    assert split_variant("trắng,1L") == {"size": "1L", "color": "trắng"}
    assert split_variant("50ml") == {"size": "50ml", "color": None}
    assert split_variant("xanh navy") == {"size": None, "color": "xanh navy"}
    assert split_variant("XL, be") == {"size": "XL", "color": "be"}
    assert split_variant("") == {"size": None, "color": None}


@pytest.mark.parametrize("path", LISTINGS, ids=lambda p: p.parent.name)
def test_market_listing_matches_the_shop_catalog(path):
    """Sau chuẩn hóa: tên giữ nguyên, giá là giá bán, biến thể khớp catalog.csv của shop."""
    import csv

    raw = json.loads(path.read_text(encoding="utf-8"))["items"]
    rows = list(csv.DictReader((path.parent / "catalog.csv").open(encoding="utf-8")))
    expected = {}
    for r in rows:
        expected.setdefault(r["sku"], set()).add(
            (r["size"] or None, r["color"] or None, int(r["stock"]))
        )
    for listing in raw:
        item = from_market_listing(listing)
        assert item.name == listing["title"]
        assert item.price_vnd == listing["price_sale"]
        assert "#" not in item.search_text and "[" not in item.search_text
        got = {(v["size"], v["color"], v["stock"]) for v in item.variants}
        assert got == expected[item.sku], item.sku


def test_market_listing_ignores_original_price_and_requires_fields():
    item = from_market_listing(
        {"item_id": 9, "title": "Ấm", "price_original": 500000, "price_sale": 250000, "models": []}
    )
    assert item.price_vnd == 250000 and item.sku == "9" and item.external_id == "9"
    with pytest.raises(ValueError):
        from_market_listing({"item_id": "1", "title": "", "price_sale": 1})
