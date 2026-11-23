"""Link trang sản phẩm (thiết kế 03 O4): nguồn catalog, kiểm tra nháp, cách hướng khách."""

import pytest
from pydantic import ValidationError

from app.modules.agent_runtime.schemas import DraftOut
from app.modules.agent_runtime.validation import validate_draft
from app.modules.knowledge.domain.catalog_csv import CatalogCsvError, parse_catalog_csv
from app.modules.knowledge.domain.catalog_source import from_market_listing
from app.modules.orders.domain.order_flow import OrderFlowConfig, plan, render_block

URL = "https://shop.vn/ao-khoac-gio"
HEADER = "sku,name,description,price_vnd,size,color,stock,url\n"


def test_csv_url_column_is_optional_and_must_be_https():
    [item] = parse_catalog_csv(
        HEADER + f"AK-02,Áo,,350000,M,đen,5,{URL}\nAK-02,Áo,,350000,L,đen,1,\n"
    )
    assert item.url == URL and len(item.variants) == 2
    no_column = "sku,name,description,price_vnd,size,color,stock\nAK-02,Áo,,1,M,đen,5\n"
    assert parse_catalog_csv(no_column)[0].url is None
    for bad in ("http://shop.vn/a", "javascript:alert(1)", "https://", "https://shop.vn/a b"):
        with pytest.raises(CatalogCsvError, match="https"):
            parse_catalog_csv(HEADER + f'AK-02,Áo,,1,M,đen,5,"{bad}"\n')
    with pytest.raises(CatalogCsvError, match="different url"):
        parse_catalog_csv(
            HEADER + f"AK-02,Áo,,1,M,đen,5,{URL}\nAK-02,Áo,,1,L,đen,5,https://shop.vn/khac\n"
        )


def test_market_listing_carries_its_url():
    raw = {"item_id": "9", "title": "Ấm", "price_sale": 1, "url": "https://shopee.vn/am-i.9"}
    assert from_market_listing(raw).url == "https://shopee.vn/am-i.9"


def _check(reply: str, urls=frozenset()):
    draft = DraftOut(reply=reply, sources=[], needs_human=False, reason="")
    return validate_draft(draft, valid_sources=set(), known_amounts=set(), known_urls=urls)


def test_draft_may_only_contain_links_the_tools_returned():
    assert _check(f"Anh/chị đặt tại đây nhé: {URL}.", frozenset({URL})) == []
    assert _check(f"Đặt tại {URL}", frozenset()) == ["url_or_html"]
    assert _check(f"[Đặt tại đây: {URL}]", frozenset({URL})) == []  # ngoặc đóng không thuộc link
    assert _check("Đặt tại https://shop.vn/ao-khoac-gio?ref=bot", frozenset({URL})) == [
        "url_or_html"
    ]
    assert _check("vào www.lua-dao.vn nhé", frozenset({URL})) == ["url_or_html"]
    assert _check("<a href=x>bấm</a>", frozenset({URL})) == ["url_or_html"]


ITEM = {"sku": "AK-02", "variant": "M đen", "needs_variant": False, "quantity": 1, "url": URL}


def test_link_destinations_ask_only_item_fields_and_fall_back_without_a_link():
    cfg = OrderFlowConfig(by_channel={"webchat": {"destination": "product_link"}})
    assert plan(cfg, "webchat", [ITEM]) == (["product", "variant", "quantity"], "product_link", URL)
    no_url = ITEM | {"url": None}
    required, destination, url = plan(cfg, "webchat", [no_url])
    assert destination == "staff_confirms" and "phone" in required and url is None
    assert plan(cfg, "zalo_oa", [ITEM])[1] == "staff_confirms"  # kênh khác giữ mặc định

    checkout = OrderFlowConfig(destination="website_checkout", checkout_url="https://shop.vn/gio")
    assert plan(checkout, "webchat", [])[1:] == ("website_checkout", "https://shop.vn/gio")
    assert plan(OrderFlowConfig(destination="website_checkout"), "webchat", [])[1] == (
        "staff_confirms"
    )
    with pytest.raises(ValidationError):
        OrderFlowConfig(checkout_url="http://shop.vn/gio")

    block = render_block(cfg, "webchat", [ITEM], {}, [])
    assert URL in block and "chép nguyên văn" in block
