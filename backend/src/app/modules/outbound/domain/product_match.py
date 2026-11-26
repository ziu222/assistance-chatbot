"""Khách gọi đúng tên/mã sản phẩm nào, và nháp có trả lời đúng sản phẩm đó không (thiết kế 06).

Đo Autopilot trên 515 ca giả lập: lỗi gửi sai duy nhất là nhầm hai sản phẩm tên gần giống
("Bình giữ nhiệt mini Mini" bị trả lời bằng giá thật của "Bình giữ nhiệt mini"). Kiểm số tiền không
bắt được vì giá đó có thật trong catalog; chỉ đối chiếu sản phẩm khách gọi tên với nguồn của nháp
mới bắt được. Hàm thuần.
"""

import re
from collections import Counter
from collections.abc import Iterable, Set
from dataclasses import dataclass

from app.shared.domain.text import extract_amounts, fold

MIN_NAME_CHARS = 6  # tên ngắn hơn ("Áo", "Mini") quá chung, khớp nhầm nhiều


@dataclass(frozen=True)
class CatalogName:
    key: str  # id sản phẩm (hoặc SKU khi replay eval)
    sku: str
    name: str


def _contains(haystack: str, needle: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack) is not None


def named_products(customer_text: str, catalog: Iterable[CatalogName]) -> set[str]:
    """Sản phẩm khách gọi đúng mã SKU hoặc đủ tên.

    - Tên dài nhất thắng: khách viết "Bình giữ nhiệt mini Mini" thì không tính "Bình giữ nhiệt
      mini" (tên ngắn nằm trong tên dài đã khớp).
    - Tên là tên chung của cả dòng ("Ấm siêu tốc V3" so với "Ấm siêu tốc Classic V3"...) thì
      khách có thể hỏi cả dòng: mọi mẫu có đủ các từ của tên đó đều tính là được gọi tên.
    """
    text = fold(customer_text)
    items = [(item, fold(item.name)) for item in catalog]
    by_sku = {item.key for item, _ in items if item.sku and _contains(text, fold(item.sku))}
    matched = {n for _, n in items if len(n) >= MIN_NAME_CHARS and _contains(text, n)}
    longest = {n for n in matched if not any(n != o and _within(n, o) for o in matched)}
    family = {item.key for item, name in items if any(_within(n, name) for n in longest)}
    return by_sku | family


def _within(short: str, long: str) -> bool:
    """Mọi từ của `short` có trong `long`, tính cả số lần lặp: "ấm siêu tốc v3" nằm trong "ấm siêu
    tốc classic v3", nhưng "bình giữ nhiệt mini mini" không nằm trong "... mini classic"."""
    return not Counter(short.split()) - Counter(long.split())


def product_mismatch(named: Set[str], cited: Set[str], reply_text: str) -> bool:
    """True ⇒ chặn: khách gọi tên sản phẩm, nháp nói về sản phẩm hay giá mà không trích nguồn sản
    phẩm nào khách gọi. Nháp không nhắc sản phẩm nào và không có số tiền (vd hỏi lại) thì qua."""
    if not named or named & cited:
        return False
    return bool(cited) or bool(extract_amounts(reply_text))
