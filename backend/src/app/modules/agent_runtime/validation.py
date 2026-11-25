"""Kiểm tra bản nháp bằng quy tắc xác định (bước 5; bất biến 4 và §7.4).

Lớp bảo vệ tin cậy nhất: không dựa vào lời tự đánh giá của model. Sai ⇒ `grounded=false`,
bản nháp vẫn được lưu để phân tích nhưng không bao giờ đủ điều kiện gửi.
"""

import re

from app.modules.agent_runtime.schemas import DraftOut
from app.modules.orders.domain.order_flow import claims_order_placed
from app.shared.domain.text import extract_amounts

MAX_REPLY = 1200
_HTML = re.compile(r"<\s*/?\s*[a-z!]", re.IGNORECASE)
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
_URL_TAIL = ".,;:!?)]}…"  # dấu câu/ngoặc đóng ngay sau link


def validate_draft(
    draft: DraftOut,
    *,
    valid_sources: set[tuple[str, str]],
    known_amounts: set[int],
    known_urls: frozenset[str] = frozenset(),
) -> list[str]:
    """Danh sách vấn đề (rỗng = hợp lệ).

    `known_amounts`: giá tool đã trả + số tiền trong FAQ/tài liệu.

    ponytail: tổng nhiều món không được chấp nhận (chỉ giá niêm yết); quá chặt còn hơn báo sai giá.
    """
    problems: list[str] = []
    if any((s.type, s.id) not in valid_sources for s in draft.sources):
        problems.append("source_not_provided")
    # Link chỉ hợp lệ khi đúng nguyên văn một link tool vừa trả hoặc chủ shop đã cấu hình (O4).
    urls = [u.rstrip(_URL_TAIL) for u in _URL.findall(draft.reply)]
    if _HTML.search(draft.reply) or any(u not in known_urls for u in urls):
        problems.append("url_or_html")
    if not 1 <= len(draft.reply.strip()) <= MAX_REPLY:
        problems.append("length")
    if not extract_amounts(draft.reply) <= known_amounts:
        problems.append("amount_mismatch")
    if claims_order_placed(draft.reply):  # chỉ nhân viên xác nhận đơn (ADR-0035)
        problems.append("order_claim")
    return problems
