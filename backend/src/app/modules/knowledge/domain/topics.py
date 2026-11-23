"""Chủ đề và độ phủ tri thức (thiết kế bước 2 §13.3, ADR-0034): hàm thuần, không I/O.

Shop không bắt buộc có tài liệu đầy đủ. Hệ thống đoán mỗi FAQ/tài liệu nói về chủ đề nào (từ khóa
trên chữ đã bỏ dấu; chủ shop sửa được), rồi biết shop **chưa** viết chủ đề nào để: (1) bot không
đoán (dòng "Shop chưa có thông tin về" trong prompt), (2) gợi ý chủ shop bổ sung.

Độ phủ là dẫn xuất (ADR-0030): luôn tính lại được từ FAQ/tài liệu, không phải nguồn sự thật riêng.
"""

from collections.abc import Iterable

from app.modules.knowledge.domain.text import fold

# Thứ tự cố định: dòng trong prompt phải ổn định byte để còn được cache (ADR-0028).
TOPICS: dict[str, str] = {
    "hours": "giờ làm việc",
    "shipping": "phí giao hàng",
    "payment": "thanh toán và COD",
    "returns": "đổi trả",
    "warranty": "bảo hành",
    "wholesale": "mua sỉ",
    "promo": "khuyến mãi",
    "invoice": "hóa đơn",
}
# Đã fold. Chọn cụm đặc trưng để mục này không "dính" sang mục khác (vd đoạn đổi trả có nhắc
# "phí ship hai chiều" không được tính là đã có phí giao hàng).
KEYWORDS: dict[str, tuple[str, ...]] = {
    "hours": ("gio lam viec:", "mo cua", "thu hai", "thu bay", "chu nhat", "hang ngay:", "nghi le",
              "lam viec gio", "dong cua"),
    "shipping": ("phi giao", "noi thanh", "ngoai thanh", "mien phi giao", "freeship",
                 "thoi gian giao", "giao di tinh", "cuoc giao", "phi van chuyen"),
    "payment": ("cod", "thanh toan khi nhan", "chuyen khoan truoc", "thu ho"),
    "returns": ("doi tra", "doi hang", "tra hang", "hoan hang"),
    "warranty": ("bao hanh",),
    "wholesale": ("gia si", "mua si", "ban si", "so luong lon"),
    "promo": ("khuyen mai", "ma giam gia", "giam gia"),
    "invoice": ("hoa don", "vat"),
}  # fmt: skip


def suggest_topics(text: str) -> list[str]:
    folded = fold(text)
    return [t for t, words in KEYWORDS.items() if any(w in folded for w in words)]


def effective_topics(stored: list[str] | None, text: str) -> list[str]:
    """`stored` = chủ shop đã chọn (kể cả danh sách rỗng); None = để hệ thống đoán."""
    return [t for t in stored if t in TOPICS] if stored is not None else suggest_topics(text)


def covered(topic_lists: Iterable[Iterable[str]]) -> set[str]:
    return {t for topics in topic_lists for t in topics}


def missing_line(cover: set[str]) -> str:
    """Dòng thêm vào cuối khối tri thức; rỗng nếu shop đã viết đủ mọi chủ đề."""
    missing = [label for t, label in TOPICS.items() if t not in cover]
    if not missing:
        return ""
    return (
        "Shop chưa có thông tin về: " + ", ".join(missing) + ". Khách hỏi các chủ đề này thì nói "
        "sẽ nhờ nhân viên hỗ trợ (needs_human=true, reason=no_source), không đoán."
    )
