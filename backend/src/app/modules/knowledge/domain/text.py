import unicodedata


def fold(value: str) -> str:
    """Chuẩn hóa để tìm kiếm: bỏ dấu tiếng Việt (kể cả đ), chữ thường, gộp khoảng trắng.

    Dùng cho cả dữ liệu lúc lưu và câu hỏi lúc tìm, nên "ao khoac" khớp "Áo khoác".
    """
    decomposed = unicodedata.normalize("NFD", value.replace("đ", "d").replace("Đ", "D"))
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return " ".join(stripped.lower().split())
