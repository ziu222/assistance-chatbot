"""Quy trình đặt hàng của từng shop và phiếu đặt hàng (thiết kế 03 §4–§5, ADR-0035): hàm thuần.

Code quyết định còn thiếu gì; model chỉ soạn câu hỏi cho trường tiếp theo. Bot không bao giờ
xác nhận đơn: câu "đã đặt thành công" bị bắt ở bước kiểm tra nháp.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.knowledge.domain.catalog_csv import parse_url
from app.modules.knowledge.domain.text import fold

Field_ = Literal[
    "product", "variant", "quantity", "name", "phone", "address", "delivery_time", "note"
]
FIELDS: tuple[str, ...] = Field_.__args__  # thứ tự cố định: bot hỏi theo thứ tự này
LABELS = {
    "product": "sản phẩm",
    "variant": "size/màu",
    "quantity": "số lượng",
    "name": "tên người nhận",
    "phone": "số điện thoại",
    "address": "địa chỉ nhận hàng",
    "delivery_time": "thời gian nhận hàng",
    "note": "ghi chú",
}
Destination = Literal["staff_confirms", "product_link", "marketplace_link", "website_checkout"]


class ChannelOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: Destination | None = None
    required_fields: list[Field_] | None = None


class OrderFlowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    required_fields: list[Field_] = ["product", "variant", "quantity", "name", "phone", "address"]
    payment_methods: list[Literal["cod", "bank_transfer"]] = ["cod"]
    payment_text: str | None = Field(default=None, max_length=1000)  # chủ shop viết, nguyên văn
    destination: Destination = "staff_confirms"
    confirm_text: str = Field(
        default="Dạ em ghi nhận đơn của anh/chị, nhân viên sẽ liên hệ xác nhận ạ.",
        min_length=1,
        max_length=500,
    )
    by_channel: dict[str, ChannelOverride] = {}
    checkout_url: str | None = None  # trang thanh toán của website (destination website_checkout)

    @field_validator("checkout_url")
    @classmethod
    def _https(cls, v: str | None) -> str | None:
        return parse_url(v)

    def for_channel(self, provider: str) -> tuple[list[str], str]:
        """(trường bắt buộc, destination) sau khi áp ghi đè của kênh."""
        o = self.by_channel.get(provider)
        required = (o.required_fields if o and o.required_fields is not None else None) or list(
            self.required_fields
        )
        return [f for f in FIELDS if f in required], (
            o.destination if o and o.destination else None
        ) or self.destination


ITEM_FIELDS = ("product", "variant", "quantity")


def plan(
    config: OrderFlowConfig, provider: str, items: list[dict]
) -> tuple[list[str], str, str | None]:
    """(trường bắt buộc, destination thực tế, link gửi khách) — code quyết, không phải model.

    Hướng khách bằng link thì khách tự đặt trên trang đó: chỉ cần món/size/số lượng, không hỏi
    thông tin cá nhân (thiết kế 03 §4.1). Món chưa có link, hoặc chưa cấu hình trang thanh toán ⇒
    quay về nhân viên xác nhận.
    """
    required, destination = config.for_channel(provider)
    url = None
    if destination in ("product_link", "marketplace_link"):
        url = items[0].get("url") if items else None
        if items and not url:
            destination = "staff_confirms"
    elif destination == "website_checkout":
        url = config.checkout_url
        if not url:
            destination = "staff_confirms"
    if destination != "staff_confirms":
        required = [f for f in required if f in ITEM_FIELDS]
    return required, destination, url


def missing_fields(required: list[str], items: list[dict], customer: dict) -> list[str]:
    """Trường bắt buộc còn thiếu, theo thứ tự cố định. `items` lấy từ catalog, không từ model."""
    item = items[0] if items else {}
    have = {
        "product": bool(item),
        "variant": bool(item) and (not item.get("needs_variant") or bool(item.get("variant"))),
        "quantity": bool(item.get("quantity")),
    } | {f: bool(customer.get(f)) for f in ("name", "phone", "address", "delivery_time", "note")}
    return [f for f in FIELDS if f in required and not have[f]]


# Số di động Việt Nam: 0 hoặc +84 rồi 9 chữ số, cho phép dấu cách/chấm/gạch giữa các nhóm.
_PHONE = re.compile(r"(?<![\w.])(?:\+84|84|0)(?:[\s.-]?\d){9}(?![\w])")


def extract_phone(texts: list[str]) -> str | None:
    """Số điện thoại cuối cùng khách gõ, chuẩn hóa về 0xxxxxxxxx. Code tách, không nhờ model."""
    found = [m.group(0) for t in texts for m in _PHONE.finditer(t)]
    if not found:
        return None
    digits = re.sub(r"\D", "", found[-1])
    return "0" + digits[2:] if digits.startswith("84") else digits


def mask_phone(phone: str | None) -> str | None:
    return f"{phone[:4]}…{phone[-3:]}" if phone and len(phone) >= 7 else phone


# Đã bỏ dấu. Câu khẳng định đơn đã xong là việc của nhân viên, không phải của bot.
_CLAIMS = re.compile(
    r"dat (hang |don )?thanh cong|da (duoc )?xac nhan (don|dat)|don (hang )?.{0,30}da duoc xac nhan"
    r"|da len don|da chot don|da tao don"
)


def claims_order_placed(reply: str) -> bool:
    return bool(_CLAIMS.search(fold(reply)))


def render_block(
    config: OrderFlowConfig, provider: str, items: list[dict], customer: dict, missing: list[str]
) -> str:
    """Khối `<order_flow>`: đổi theo hội thoại nên đặt sau khối tri thức (ADR-0028)."""
    required, destination, url = plan(config, provider, items)
    have = [f"{LABELS[f]}: đã có" for f in required if f not in missing]
    lines = [
        "<order_flow>",
        "Khách muốn đặt hàng. Bạn KHÔNG tạo hay xác nhận đơn; nhân viên sẽ xác nhận.",
        "Tra sản phẩm bằng tool trước; hết hàng thì gợi ý mẫu cùng loại còn hàng.",
        "Điền order: sku (từ kết quả tool), variant, quantity, và các thông tin khách vừa cho.",
        "Thông tin đã có: " + ("; ".join(have) if have else "chưa có"),
    ]
    if missing and missing[0] == "product":
        # Phiếu được dựng trước khi tra kho: khách thường đã nêu món ngay trong tin này.
        rest = [LABELS[f] for f in missing[1:]]
        lines.append(
            "Phiếu chưa có sản phẩm: tra món khách nhắc tới bằng tool rồi điền sku; khách chưa "
            "nêu món nào thì hỏi khách muốn đặt món nào."
            + (f" Sau đó hỏi MỘT mục tiếp theo, theo thứ tự: {', '.join(rest)}." if rest else "")
        )
    elif missing:
        lines.append(
            f"Còn thiếu: {', '.join(LABELS[f] for f in missing)}. "
            f"Chỉ hỏi MỘT mục: {LABELS[missing[0]]}."
        )
    elif destination == "staff_confirms":
        lines.append(f'Đã đủ thông tin. Trả lời đúng câu sau: "{config.confirm_text}"')
    else:
        lines.append(
            f"Đã đủ thông tin. Gửi khách link sau để tự đặt hàng, chép nguyên văn, không sửa: {url}"
        )
    if config.payment_text:
        lines.append(f'Khách hỏi thanh toán thì dùng đúng câu: "{config.payment_text}"')
    lines.append("</order_flow>")
    return "\n".join(lines)
