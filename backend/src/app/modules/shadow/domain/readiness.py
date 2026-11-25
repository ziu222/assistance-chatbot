"""Mức sẵn sàng tự trả lời theo ý định (thiết kế 04 §7, ADR-0037): hàm thuần.

Chỉ là ĐỀ XUẤT cho bước 5; chủ shop quyết. Số đo của một phiên bản cấu hình AI (template,
prompt, tri thức) không nói gì về phiên bản khác, nên mỗi phiên bản đếm lại từ đầu.
"""

from dataclasses import dataclass, field
from typing import Literal

MIN_SCORED = 30
MAX_WRONG_RATE = 0.02
MIN_SENDABLE_RATE = 0.85
SHOWN_MINOR_EDIT_MAX = 0.2

MeasurementSource = Literal["blind", "shown"]


@dataclass(frozen=True)
class ShownUseGrade:
    sendable: bool
    wrong: bool


def classify_shown_use(
    action: str,
    edit_ratio: float,
    diff_features: dict | None,
    *,
    amount_conflict: bool = False,
) -> ShownUseGrade:
    """Đánh giá một cặp `draft_exposure = 'shown'` theo mức sửa (thiết kế 05 §7, S5)."""
    df = diff_features or {}
    amount_changed = bool(df.get("amounts_added") or df.get("amounts_removed"))
    wrong = amount_conflict or (action == "edited" and amount_changed)
    sendable = not wrong and (
        action == "as_is"
        or (action == "edited" and edit_ratio < SHOWN_MINOR_EDIT_MAX and not amount_changed)
    )
    return ShownUseGrade(sendable=sendable, wrong=wrong)


def intent_priority_score(wrong_rate: float | None, discard_rate: float | None) -> float:
    """Điểm ưu tiên ứng viên theo ý định: tỉ lệ sai và tỉ lệ bỏ càng cao càng ưu tiên (05 §7)."""
    return round(2.0 * (wrong_rate or 0.0) + (discard_rate or 0.0), 4)


@dataclass(frozen=True)
class IntentStats:
    intent: str | None
    total: int  # mọi nháp đã xử lý (kể cả bị thay, chưa ai trả lời)
    unanswered: int
    scored: int  # có kết luận cuối (code hoặc giám khảo)
    sendable: int
    wrong: int  # sai hoặc không được gửi
    amount_conflicts_7d: int
    handoff_extra: int
    handoff_missing: int
    median_bot_lead_s: float | None
    models: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Readiness:
    ready: bool
    reasons: list[str]  # vì sao CHƯA sẵn sàng; rỗng khi sẵn sàng


def rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def readiness(
    s: IntentStats,
    *,
    judge_calibrated: bool,
    source: MeasurementSource = "blind",
) -> Readiness:
    reasons: list[str] = []
    if s.scored < MIN_SCORED:
        reasons.append(f"chưa đủ mẫu ({s.scored}/{MIN_SCORED} cặp được chấm)")
    else:
        wrong, sendable = rate(s.wrong, s.scored) or 0.0, rate(s.sendable, s.scored) or 0.0
        if wrong > MAX_WRONG_RATE:
            reasons.append(f"tỉ lệ sai {wrong:.0%} (cần ≤ {MAX_WRONG_RATE:.0%})")
        if sendable < MIN_SENDABLE_RATE:
            reasons.append(f"tỉ lệ gửi được {sendable:.0%} (cần ≥ {MIN_SENDABLE_RATE:.0%})")
    if s.amount_conflicts_7d:
        reasons.append(f"{s.amount_conflicts_7d} lần báo giá mâu thuẫn trong 7 ngày")
    if source == "blind" and not judge_calibrated:
        reasons.append("giám khảo chưa được hiệu chỉnh với nhãn của chủ shop")
    return Readiness(not reasons, reasons)
