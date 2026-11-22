"""Tín hiệu leo thang ở chế độ CHỈ GHI (thiết kế bước 2 §12.7, ADR-0032).

Hàm thuần, không I/O. Điểm được ghi vào trace để đo precision/recall so với nhãn thật (ticket
mở và nhãn "không cần" của nhân viên) TRƯỚC KHI cho nó điều khiển gì. Bằng chứng ngoài
(COLING 2025: precision ~0,7, đồng thuận người gán nhãn kappa 0,48) chưa đủ để tin trên chat
tiếng Việt ngắn, thiếu dấu.

Trọng số và ngưỡng là giá trị khởi điểm để đo, không phải kết luận.
"""

from typing import Literal

from app.modules.knowledge.domain.text import fold

# Đã fold. Khách phủ nhận câu trả lời hoặc than chưa được giải quyết.
NEGATION_PHRASES = (
    "khong phai",
    "khong hieu",
    "sai roi",
    "khong dung",
    "noi roi",
    "hoi roi",
    "da noi",
    "da hoi",
    "van chua",
    "chua tra loi",
    "tra loi di",
    "may moc",
)
WEIGHTS: dict[str, int] = {
    "repeat": 2,  # lặp lại câu hỏi đã hỏi
    "negation": 1,
    "burst": 1,  # nhắn dồn ≥ 4 tin trong một cửa sổ gom tin
    "long_unresolved": 1,  # shop đã trả lời ≥ 3 lượt mà khách vẫn hỏi
    "shouting": 1,  # "!!", "??"
    "frustration_mild": 1,
    "frustration_high": 3,
    "human_decision": 2,  # cần người quyết (ngoại lệ chính sách, giảm giá riêng…)
}
Sensitivity = Literal["low", "medium", "high"]
THRESHOLDS: dict[Sensitivity, int] = {"low": 5, "medium": 4, "high": 3}
SEMANTIC_MIN_TURNS = 4  # dưới mức này tín hiệu ngữ nghĩa nhiễu, không tính
REPEAT_SIMILARITY = 0.6


def _trigrams(text: str) -> set[str]:
    t = f"  {fold(text)} "
    return {t[i : i + 3] for i in range(len(t) - 2)}


def _similar(a: str, b: str) -> float:
    ta, tb = _trigrams(a), _trigrams(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def behavior_signals(
    earlier_customer: list[str], new_customer: list[str], shop_turns: int
) -> list[str]:
    """`earlier_customer`: tin khách trong lịch sử; `new_customer`: tin khách của cụm này;
    `shop_turns`: số lượt shop (nhân viên/tin tự động) trong lịch sử."""
    if not new_customer:
        return []
    found: list[str] = []
    latest = new_customer[-1]
    if len(fold(latest)) >= 8 and any(
        len(fold(e)) >= 8 and _similar(latest, e) >= REPEAT_SIMILARITY for e in earlier_customer
    ):
        found.append("repeat")
    joined = " ".join(fold(t) for t in new_customer)
    if any(p in joined for p in NEGATION_PHRASES):
        found.append("negation")
    if len(new_customer) >= 4:
        found.append("burst")
    if shop_turns >= 3:
        found.append("long_unresolved")
    if any("!!" in t or "??" in t for t in new_customer):
        found.append("shouting")
    return found


def semantic_signals(frustration: str, human_decision: bool, turns: int) -> list[str]:
    if turns < SEMANTIC_MIN_TURNS:
        return []
    found = [f"frustration_{frustration}"] if frustration in ("mild", "high") else []
    return found + (["human_decision"] if human_decision else [])


def score(signals: list[str]) -> int:
    return sum(WEIGHTS[s] for s in signals)
