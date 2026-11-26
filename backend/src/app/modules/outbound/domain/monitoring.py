"""Quy tắc theo dõi sau gửi và tự hạ cấp Autopilot (thiết kế 06 §6, A4): hàm thuần.

Không có câu nhân viên độc lập để so như Shadow, nên Autopilot theo dõi bằng 4 tín hiệu:
- nhân viên đính chính trong 10 phút sau tin bot (trước khi khách trả lời);
- khách phản ứng xấu (`complaint`, `human_request`, hoặc hỏi lại gần như nguyên văn);
- chủ shop chấm mẫu (tối đa 10 tin/ngày/ý định);
- kiểm số tiền sau gửi đối chiếu catalog/tri thức hiện hành.
"""

from difflib import SequenceMatcher
from typing import Literal

from app.modules.learning.domain.draft_use import compare
from app.shared.domain.text import fold

CORRECTION_WINDOW_SECONDS = 600  # 10 phút sau tin bot
REACTION_WINDOW_SECONDS = 24 * 3600  # phản ứng cách tin bot quá 24 giờ thì không tính cho tin đó
DEMOTE_WINDOW_REPLIES = 50  # cửa sổ 50 tin bot gần nhất
MAX_STAFF_AMOUNT_CORRECTION_RATE = 0.02  # > 2% ⇒ hạ cấp
MAX_CUSTOMER_REACTION_RATE = 0.05  # > 5% ⇒ hạ cấp
REPEAT_SIMILARITY_THRESHOLD = 0.85
MIN_REPEAT_CHARS = 8
DAILY_SAMPLE_LIMIT_PER_INTENT = 10
BAD_REACTION_INTENTS = frozenset({"complaint", "human_request"})

OwnerReplyLabel = Literal["correct", "minor_edit", "wrong"]


def is_repeat_question(prev_customer: str, next_customer: str) -> bool:
    """Khách hỏi lại gần như nguyên văn sau khi bot trả lời."""
    a, b = fold(prev_customer), fold(next_customer)
    if len(a) < MIN_REPEAT_CHARS or len(b) < MIN_REPEAT_CHARS:
        return False
    return SequenceMatcher(None, a, b).ratio() >= REPEAT_SIMILARITY_THRESHOLD


def classify_staff_correction(bot_text: str, staff_text: str) -> dict:
    """So tin nhân viên đính chính với tin bot bằng `diff_features` (không lưu chữ)."""
    use = compare(bot_text, staff_text, used=True)
    features = use.features()
    # Chỉ tính khi nhân viên đưa ra số tiền KHÁC. Câu hỏi thăm không nhắc lại giá của bot
    # ("chị cần gì thêm ạ") làm `amounts_removed` khác rỗng nhưng không phải đính chính.
    amount_changed = bool(features.get("amounts_added"))
    return {
        "edit_ratio": use.edit_ratio,
        "amount_changed": amount_changed,
        "diff_features": features,
    }


def demotion_reason(
    recent_reply_count: int,
    *,
    amount_conflicts: int = 0,
    owner_wrong_fact: int = 0,
    staff_amount_corrections: int = 0,
    customer_reactions: int = 0,
) -> str | None:
    """Trả lý do tự hạ cấp ngay nếu vượt ngưỡng §6.2, ngược lại trả `None`."""
    if owner_wrong_fact > 0:
        return "owner_label_wrong"
    if amount_conflicts > 0:
        return "amount_conflict"
    denom = max(recent_reply_count, DEMOTE_WINDOW_REPLIES)
    if staff_amount_corrections / denom > MAX_STAFF_AMOUNT_CORRECTION_RATE:
        return "staff_correction_rate"
    if customer_reactions / denom > MAX_CUSTOMER_REACTION_RATE:
        return "customer_reaction_rate"
    return None
