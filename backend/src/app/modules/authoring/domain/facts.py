"""Kiểm tra giữ sự thật cho trợ lý viết (thiết kế bước 2 §12.8): hàm thuần, không I/O.

LLM chỉ được đổi câu chữ. Mọi "sự thật" trong bản gốc của chủ shop (số tiền, thời hạn, giờ, số
điện thoại, link, biến `{shop_name}`/`{next_open}`) phải còn nguyên trong gợi ý, và gợi ý không
được thêm sự thật mới. Lệch ⇒ nút "Dùng" bị khóa. Lời hứa mới (freeship, tặng quà…) ⇒ cảnh báo.
"""

import re
from dataclasses import dataclass, field

from app.shared.domain.text import extract_amounts, fold

_DURATION = re.compile(r"\b(\d+)\s*(ngay|tuan|thang|nam|gio|tieng|phut|h)\b")
_UNIT = {"tieng": "gio", "h": "gio"}
_CLOCK = re.compile(r"\b(\d{1,2})\s*(?::|h)\s*(\d{2})?\b")
_PHONE = re.compile(r"(?<!\d)(0\d(?:[\s.-]?\d){8,9})(?!\d)")
_LINK = re.compile(r"(https?://\S+|www\.\S+|\b[\w.-]+@[\w-]+\.[\w.]+\b)", re.IGNORECASE)
_VARIABLE = re.compile(r"\{[a-z_]+\}")
# Đã fold. Cụm hứa hẹn/khuyến mãi mà chủ shop chưa viết thì gợi ý không được tự thêm.
PROMISES = (
    "freeship", "mien phi van chuyen", "mien phi giao", "mien phi ship", "tang kem", "qua tang",
    "tang qua", "hoan tien", "giam gia", "khuyen mai", "bao hanh tron doi", "doi tra mien phi",
    "cam ket", "100%", "dam bao",
)  # fmt: skip


def facts(text: str) -> dict[str, set[str]]:
    """Sự thật trong một đoạn văn, đã chuẩn hóa để so sánh."""
    folded = fold(text)
    durations = {f"{n} {_UNIT.get(u, u)}" for n, u in _DURATION.findall(folded)}
    clocks = {f"{int(h)}:{m or '00'}" for h, m in _CLOCK.findall(folded) if int(h) < 24}
    return {
        "amounts": {str(a) for a in extract_amounts(text)},
        "durations": durations,
        "times": clocks,
        "phones": {re.sub(r"\D", "", p) for p in _PHONE.findall(text)},
        "links": {x.lower().rstrip(".,)") for x in _LINK.findall(text)},
        "variables": set(_VARIABLE.findall(text)),
    }


@dataclass(frozen=True)
class FactCheck:
    missing: dict[str, list[str]] = field(default_factory=dict)  # có ở bản gốc, mất ở gợi ý
    added: dict[str, list[str]] = field(default_factory=dict)  # gợi ý tự thêm
    new_promises: list[str] = field(default_factory=list)  # cảnh báo, không khóa

    @property
    def ok(self) -> bool:
        """True ⇒ được bấm "Dùng"."""
        return not self.missing and not self.added

    def as_json(self) -> dict:
        return {"ok": self.ok, "missing": self.missing, "added": self.added,
                "new_promises": self.new_promises}  # fmt: skip


def check(original: str, suggestion: str) -> FactCheck:
    before, after = facts(original), facts(suggestion)
    missing = {k: sorted(before[k] - after[k]) for k in before if before[k] - after[k]}
    added = {k: sorted(after[k] - before[k]) for k in after if after[k] - before[k]}
    o, s = fold(original), fold(suggestion)
    promises = [p for p in PROMISES if p in s and p not in o]
    return FactCheck(missing, added, promises)
