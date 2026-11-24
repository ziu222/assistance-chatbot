"""Mức sẵn sàng tự trả lời theo ý định (thiết kế 04 §7, ADR-0037): hàm thuần.

Chỉ là ĐỀ XUẤT cho bước 5; chủ shop quyết. Số đo của một phiên bản cấu hình AI (template,
prompt, tri thức) không nói gì về phiên bản khác, nên mỗi phiên bản đếm lại từ đầu.
"""

from dataclasses import dataclass, field

MIN_SCORED = 30
MAX_WRONG_RATE = 0.02
MIN_SENDABLE_RATE = 0.85


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


def readiness(s: IntentStats, *, judge_calibrated: bool) -> Readiness:
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
    if not judge_calibrated:
        reasons.append("giám khảo chưa được hiệu chỉnh với nhãn của chủ shop")
    return Readiness(not reasons, reasons)
