"""Hiệu chỉnh giám khảo với nhãn của chủ shop (thiết kế 04 §6.3, ADR-0037): hàm thuần.

Chủ shop gán một trong ba nhãn cho mỗi cặp. Mức gửi được của giám khảo quy về cùng ba nhãn đó rồi
đo Cohen's kappa. Chưa đủ nhãn hoặc kappa thấp ⇒ điểm giám khảo chỉ để tham khảo.
"""

from collections import Counter
from dataclasses import dataclass
from typing import Literal

Label = Literal["sendable", "rewrite", "wrong"]
LABELS: tuple[Label, ...] = ("sendable", "rewrite", "wrong")
MIN_LABELS = 30
MIN_KAPPA = 0.6


def judge_label(sendable: str) -> Label:
    """Mức gửi được của giám khảo ⇒ nhãn cùng thang với chủ shop."""
    if sendable in ("as_is", "minor_edit"):
        return "sendable"
    return "rewrite" if sendable == "rewrite" else "wrong"


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Kappa cho các cặp (giám khảo, người). None khi không có cặp hoặc không tính được."""
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(a == b for a, b in pairs) / n
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(left[k] * right[k] for k in set(left) | set(right)) / (n * n)
    if expected == 1:  # cả hai luôn cùng một nhãn: không có gì để phân biệt
        return 1.0 if observed == 1 else None
    return (observed - expected) / (1 - expected)


@dataclass(frozen=True)
class Calibration:
    labels: int
    kappa: float | None
    agreement: float | None  # khớp gửi được / không gửi được (hai nhóm)
    calibrated: bool


def calibrate(pairs: list[tuple[str, str]]) -> Calibration:
    """`pairs`: (nhãn quy đổi từ giám khảo, nhãn của chủ shop)."""
    kappa = cohen_kappa(pairs)
    agreement = (
        sum((a == "sendable") == (b == "sendable") for a, b in pairs) / len(pairs)
        if pairs
        else None
    )
    ok = len(pairs) >= MIN_LABELS and kappa is not None and kappa >= MIN_KAPPA
    return Calibration(len(pairs), kappa, agreement, ok)
