"""Quy trình đặt hàng và phiếu đặt hàng (thiết kế 03, ADR-0035)."""

import pytest
from pydantic import ValidationError

from app.modules.orders.domain.order_flow import (
    OrderFlowConfig,
    claims_order_placed,
    extract_phone,
    mask_phone,
    missing_fields,
    render_block,
)

ITEM = {"sku": "AK-02", "variant": "M", "needs_variant": True, "quantity": 1}


def test_channel_override_changes_destination_and_fields():
    cfg = OrderFlowConfig(
        by_channel={"webchat": {"destination": "product_link", "required_fields": ["product"]}}
    )
    assert cfg.for_channel("webchat") == (["product"], "product_link")
    assert cfg.for_channel("zalo_oa") == (
        ["product", "variant", "quantity", "name", "phone", "address"],
        "staff_confirms",
    )
    with pytest.raises(ValidationError):
        OrderFlowConfig(required_fields=["email"])


def test_missing_fields_in_fixed_order():
    required = ["product", "variant", "quantity", "name", "phone", "address"]
    assert missing_fields(required, [], {}) == required
    no_variant = ITEM | {"variant": None}
    assert missing_fields(required, [no_variant], {"phone": "0912345678"}) == [
        "variant",
        "name",
        "address",
    ]
    one_size = ITEM | {"variant": None, "needs_variant": False}
    customer = {"name": "Lan", "phone": "0912345678", "address": "12 Lê Lợi"}
    assert missing_fields(required, [one_size], customer) == []


@pytest.mark.parametrize(
    ("texts", "phone"),
    [
        (["sdt em 0912 345 678 nha"], "0912345678"),
        (["+84 912.345.678"], "0912345678"),
        (["số cũ 0901111111", "à nhầm, 0988-222-333"], "0988222333"),
        (["đơn DH-PK-0059 giá 179.000đ"], None),
        (["mã 012345678901234"], None),  # dãy dài hơn không phải số điện thoại
    ],
)
def test_extract_phone(texts, phone):
    assert extract_phone(texts) == phone


def test_mask_phone():
    assert mask_phone("0912345678") == "0912…678"


@pytest.mark.parametrize(
    ("reply", "claim"),
    [
        ("Dạ đơn của anh/chị đã được xác nhận ạ", True),
        ("Em đã đặt hàng thành công cho chị", True),
        ("Dạ em đã lên đơn rồi ạ", True),
        ("Dạ em ghi nhận, nhân viên sẽ liên hệ xác nhận ạ", False),
        ("Anh/chị cho em xin số điện thoại để nhân viên xác nhận đơn nhé", False),
    ],
)
def test_claims_order_placed(reply, claim):
    assert claims_order_placed(reply) is claim


def test_block_asks_one_field_or_uses_owner_text():
    cfg = OrderFlowConfig(confirm_text="Shop gọi lại trong 30 phút ạ", payment_text="COD toàn quốc")
    asking = render_block(cfg, "webchat", [], {}, ["product", "phone"])
    assert "tra món khách nhắc tới" in asking and "theo thứ tự: số điện thoại" in asking
    assert "COD toàn quốc" in asking
    assert "Chỉ hỏi MỘT mục: số điện thoại" in render_block(cfg, "webchat", [ITEM], {}, ["phone"])
    done = render_block(cfg, "webchat", [ITEM], {}, [])
    assert '"Shop gọi lại trong 30 phút ạ"' in done
