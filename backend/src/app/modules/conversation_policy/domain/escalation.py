"""Đo tín hiệu leo thang so với nhãn thật (bước 2 §12.7): hàm thuần.

Nhãn dương = hội thoại có ticket mà nhân viên KHÔNG đánh dấu "không cần". Dự đoán dương = điểm
cao nhất của hội thoại ≥ ngưỡng của độ nhạy. Chỉ để chủ shop/kỹ sư nhìn số trước khi cho điểm
điều khiển bất cứ thứ gì.
"""

from dataclasses import asdict, dataclass

from app.modules.agent_runtime.signals import THRESHOLDS, Sensitivity


def ticket_priority(source: str, escalation_score: int, sensitivity: Sensitivity = "low") -> int:
    """Tính mức ưu tiên ticket (0: thường, 1: cao, 2: khẩn cấp) từ nguồn mở và điểm leo thang.

    Chỉ sắp xếp hàng đợi nhân viên, không tự động mở ticket (giữ bất biến E14).
    """
    if escalation_score >= THRESHOLDS[sensitivity]:
        return 2
    if source in ("customer_button", "phrase") or escalation_score >= 2:
        return 1
    return 0


@dataclass(frozen=True)
class Confusion:
    threshold: int
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    def as_json(self) -> dict:
        return asdict(self) | {"precision": self.precision, "recall": self.recall}


def confusion(rows: list[tuple[int, bool]], threshold: int) -> Confusion:
    """`rows`: (điểm cao nhất, cần người thật) cho mỗi hội thoại."""
    tp = sum(1 for s, y in rows if s >= threshold and y)
    fp = sum(1 for s, y in rows if s >= threshold and not y)
    fn = sum(1 for s, y in rows if s < threshold and y)
    return Confusion(threshold, tp, fp, fn, len(rows) - tp - fp - fn)


def report(rows: list[tuple[int, bool]]) -> dict:
    return {
        "conversations": len(rows),
        "needed_human": sum(1 for _, y in rows if y),
        "by_sensitivity": {k: confusion(rows, t).as_json() for k, t in THRESHOLDS.items()},
    }
