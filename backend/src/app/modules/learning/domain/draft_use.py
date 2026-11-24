"""Nhân viên đã làm gì với nháp (thiết kế 05 §4.2): hàm thuần.

`as_is` = chữ gửi trùng nháp sau khi chuẩn hóa khoảng trắng; `edited` = sửa rồi gửi; `discarded` =
bấm Bỏ hoặc tự gõ câu khác. `diff_features` là những gì code đọc được (số tiền, link, câu thêm/bớt),
dùng để học và để đo; không chứa chữ.
"""

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Literal

from app.modules.agent_runtime.validation import extract_amounts

Action = Literal["as_is", "edited", "discarded"]
MINOR_EDIT = 0.2  # dưới mức này coi là sửa nhẹ (thiết kế 05 §7)
_URL = re.compile(r"https?://[^\s<>\"']+")
_SENTENCE = re.compile(r"[.!?\n]+")


def _norm(text: str) -> str:
    return " ".join(text.split())


def _sentences(text: str) -> set[str]:
    return {_norm(s).lower() for s in _SENTENCE.split(text) if len(_norm(s)) >= 3}


@dataclass(frozen=True)
class DraftUse:
    action: Action
    edit_ratio: float
    amounts_added: list[int]
    amounts_removed: list[int]
    links_changed: bool
    sentences_added: int
    sentences_removed: int

    def features(self) -> dict:
        d = asdict(self)
        del d["action"], d["edit_ratio"]
        return d

    @property
    def minor(self) -> bool:
        """Sửa nhẹ: ít chữ và không đổi số tiền hay link."""
        return (
            self.action == "as_is"
            or self.action == "edited"
            and self.edit_ratio < MINOR_EDIT
            and not self.amounts_added
            and not self.amounts_removed
            and not self.links_changed
        )


def compare(draft: str, sent: str | None, *, used: bool) -> DraftUse:
    """`used` = nhân viên bấm Dùng/Sửa (gửi kèm draft_id); `sent` None = bấm Bỏ, không gửi gì."""
    if sent is None or not used:
        return DraftUse("discarded", 1.0, [], [], False, 0, 0)
    a, b = _norm(draft), _norm(sent)
    ratio = round(1 - SequenceMatcher(None, a, b).ratio(), 3)
    da, ds = extract_amounts(draft), extract_amounts(sent)
    return DraftUse(
        action="as_is" if a == b else "edited",
        edit_ratio=0.0 if a == b else ratio,
        amounts_added=sorted(ds - da),
        amounts_removed=sorted(da - ds),
        links_changed=set(_URL.findall(draft)) != set(_URL.findall(sent)),
        sentences_added=len(_sentences(sent) - _sentences(draft)),
        sentences_removed=len(_sentences(draft) - _sentences(sent)),
    )
