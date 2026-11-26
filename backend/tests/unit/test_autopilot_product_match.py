"""Đối chiếu sản phẩm khách gọi tên với nguồn của nháp (thiết kế 06, đo Autopilot)."""

from app.modules.outbound.domain.product_match import (
    CatalogName,
    named_products,
    product_mismatch,
)

CATALOG = [
    CatalogName("p6", "KN-006", "Bình giữ nhiệt mini"),
    CatalogName("p7", "KN-007", "Bình giữ nhiệt mini Classic"),
    CatalogName("p8", "KN-008", "Bình giữ nhiệt mini Mini"),
    CatalogName("short", "X-1", "Mini"),
]


def test_longest_name_wins_and_works_without_diacritics():
    assert named_products("Cho minh hoi gia Binh giu nhiet mini Mini loai 500ml", CATALOG) == {"p8"}
    # Tên chung của cả dòng: khách có thể hỏi bất kỳ mẫu nào trong dòng.
    assert named_products("Bình giữ nhiệt mini giá bao nhiêu?", CATALOG) == {"p6", "p7", "p8"}


def test_sku_and_short_names():
    assert named_products("Mã kn-007 còn không shop?", CATALOG) == {"p7"}
    assert named_products("Có loại mini nào không ạ", CATALOG) == set()  # "Mini" quá ngắn


def test_mismatch_rule():
    reply = "Dạ bình giữ nhiệt mini 500ml giá 190.000đ ạ."
    assert product_mismatch({"p8"}, {"p6", "p7"}, reply) is True  # ca gửi sai đã đo được
    assert product_mismatch({"p8"}, {"p8"}, "Dạ giá 130.000đ ạ.") is False
    assert product_mismatch({"p8"}, set(), "Dạ giá 130.000đ ạ.") is True  # giá mà không có nguồn
    assert product_mismatch({"p8"}, set(), "Dạ anh/chị cần size nào ạ?") is False
    assert product_mismatch(set(), {"p6"}, reply) is False  # khách không gọi tên sản phẩm nào


def test_family_name_with_words_in_between():
    catalog = [
        CatalogName("v3", "KN-181", "Ấm siêu tốc V3"),
        CatalogName("cv3", "KN-182", "Ấm siêu tốc Classic V3"),
        CatalogName("cv2", "KN-102", "Ấm siêu tốc Classic V2"),
    ]
    assert named_products("alo shop, Ấm siêu tốc V3 con hang ko", catalog) == {"v3", "cv3"}
