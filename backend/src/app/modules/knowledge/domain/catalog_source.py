"""Chuẩn hóa sản phẩm từ mọi nguồn catalog (thiết kế bước 2 §13.2): hàm thuần, không I/O.

Mỗi nguồn (CSV, Google Sheets, sàn) đưa về `ParsedItem` + `external_id`, rồi `normalize` dựng
`search_text`. `name`/`description` giữ nguyên như nguồn (khách thấy đúng chữ đó trên sàn); chỉ
`search_text` được làm sạch. Giá luôn là **giá bán**: "giá gốc" của sàn không bao giờ vào Atlas.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.modules.knowledge.domain.catalog_csv import ParsedItem, parse_url
from app.modules.knowledge.domain.text import fold

SOURCES = ("csv", "sheets", "shopee", "tiktok")
MAX_SKU, MAX_NAME = 64, 200  # khớp CHECK của bảng catalog_items

_PROMO = re.compile(r"\[[^\]]*\]")  # "[Mã OT98 giảm 30K]"
_HASHTAG = re.compile(r"#\S+")
# ponytail: đoán size bằng mẫu chữ (S/M/XL, 28, 50ml, 1L, 128GB…); phần còn lại là màu.
_SIZE = re.compile(
    r"(?:x{0,3}[sml]|\d?xl|free ?size|\d+(?:[.,]\d+)?\s*(?:ml|l|g|kg|cm|mm|m|inch|gb|tb|mah|w)?)",
    re.I,
)


@dataclass(frozen=True)
class NormalizedItem:
    external_id: str
    sku: str
    name: str
    description: str | None
    price_vnd: int
    variants: list[dict]
    search_text: str
    url: str | None = None


def clean_for_search(value: str) -> str:
    """Bỏ ngoặc khuyến mãi, hashtag, emoji rồi bỏ dấu; chữ còn lại vẫn tìm được như trước."""
    value = _HASHTAG.sub(" ", _PROMO.sub(" ", value))
    value = "".join(
        " " if unicodedata.category(ch) in ("So", "Sk", "Cs", "Co") or ch in "‍️" else ch
        for ch in value
    )
    return fold(value)


def split_variant(name: str) -> dict[str, str | None]:
    """`"đen,M"` ⇒ màu đen, size M. Thứ tự hai phần không quan trọng."""
    sizes, colors = [], []
    for part in (p.strip() for p in name.split(",")):
        if part:
            (sizes if _SIZE.fullmatch(part) else colors).append(part)
    return {"size": " ".join(sizes) or None, "color": " ".join(colors) or None}


def normalize(item: ParsedItem, external_id: str) -> NormalizedItem:
    return NormalizedItem(
        external_id=external_id,
        sku=item.sku,
        name=item.name,
        description=item.description,
        price_vnd=item.price_vnd,
        variants=item.variants,
        search_text=clean_for_search(f"{item.name} {item.description or ''}"),
        url=item.url,
    )


def from_market_listing(raw: dict[str, Any]) -> NormalizedItem:
    """Một sản phẩm kiểu sàn (bộ giả lập `raw_listing.json`, không phải schema API thật).

    Ném ValueError nếu thiếu trường bắt buộc; người gọi ghi lỗi vào lần sync và bỏ qua dòng đó.
    """
    external_id = str(raw.get("item_id") or "").strip()
    name = str(raw.get("title") or "").strip()[:MAX_NAME]
    price = raw.get("price_sale")
    if not external_id or not name or not isinstance(price, int) or price < 0:
        raise ValueError(f"item {external_id or '?'}: missing item_id, title or price_sale")
    variants = [
        split_variant(str(m.get("name") or "")) | {"stock": max(int(m.get("stock") or 0), 0)}
        for m in raw.get("models") or []
    ]
    item = ParsedItem(
        sku=(str(raw.get("seller_sku") or "").strip() or external_id)[:MAX_SKU],
        name=name,
        description=str(raw.get("description") or "").strip() or None,
        price_vnd=price,  # giá bán; price_original bị bỏ qua có chủ đích
        variants=variants,
        url=parse_url(raw.get("url")),  # link sai ⇒ ValueError như mọi trường bắt buộc khác
    )
    return normalize(item, external_id)
