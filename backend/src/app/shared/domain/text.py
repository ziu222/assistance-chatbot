"""Chuẩn hóa văn bản tiếng Việt và trích xuất số tiền (hàm thuần dùng chung giữa các module)."""

import re
import unicodedata

# Số tiền: "350.000đ", "350k", "1,2 triệu", hoặc số có dấu phân cách nghìn dù thiếu đơn vị.
_AMOUNT = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(nghin|ngan|trieu|tr|k|vnd|dong|d)?(?![a-z0-9])"
)
_THOUSANDS = re.compile(r"\d{1,3}(?:[.,]\d{3})+")
_MULTIPLIER = {"k": 1_000, "nghin": 1_000, "ngan": 1_000, "tr": 1_000_000, "trieu": 1_000_000}


def fold(value: str) -> str:
    """Chuẩn hóa để tìm kiếm: bỏ dấu tiếng Việt (kể cả đ), chữ thường, gộp khoảng trắng.

    Dùng cho cả dữ liệu lúc lưu và câu hỏi lúc tìm, nên "ao khoac" khớp "Áo khoác".
    """
    decomposed = unicodedata.normalize("NFD", value.replace("đ", "d").replace("Đ", "D"))
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return " ".join(stripped.lower().split())


def extract_amounts(text: str) -> set[int]:
    """Các số tiền (VND) xuất hiện trong `text`. Số trần như "size 38" hay "7 ngày" bị bỏ qua."""
    found: set[int] = set()
    for number, unit in _AMOUNT.findall(fold(text)):
        thousands = bool(_THOUSANDS.fullmatch(number))
        if not unit and not thousands:
            continue
        value = int(re.sub(r"[.,]", "", number)) if thousands else float(number.replace(",", "."))
        found.add(round(value * _MULTIPLIER.get(unit, 1)))
    return found
