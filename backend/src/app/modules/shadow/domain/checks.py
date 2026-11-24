"""Chấm một cặp nháp ↔ câu nhân viên bằng code (thiết kế 04 §5): hàm thuần, chạy cho mọi cặp.

Code kết luận được thì không cần giám khảo: nháp bị Atlas chặn ⇒ không gửi được; mâu thuẫn số
tiền hay sản phẩm (mà câu nhân viên không bị nghi) ⇒ sai. Giá nhân viên nêu không có trong
catalog/tri thức ⇒ câu tham chiếu bị nghi, không tính là bot sai (§4.3).
"""

import re
from dataclasses import asdict, dataclass
from typing import Literal

from app.modules.agent_runtime.validation import extract_amounts
from app.modules.knowledge.domain.text import fold

_URL = re.compile(r"https?://[^\s<>\"']+")
# ponytail: nhận diện "để em kiểm tra" bằng cụm từ; đủ để ước lượng chuyển người ở S1.
_DEFER = (
    "de em kiem tra",
    "em kiem tra lai",
    "em hoi lai",
    "em bao lai",
    "cho em xiu",
    "doi em",
    "em check",
    "chuyen cho",
    "nhan vien se lien he",
)

Verdict = Literal["not_sendable", "wrong"]


@dataclass(frozen=True)
class Product:
    sku: str
    name: str


@dataclass(frozen=True)
class CodeChecks:
    amounts: str  # none | agree | conflict | draft_missing | draft_extra
    products: str  # unknown | match | differ
    handoff: str  # agree | extra | missing
    links: str  # none | match | differ
    reference_suspect: bool
    verdict: Verdict | None  # None ⇒ cần giám khảo (S2)

    def as_dict(self) -> dict:
        return asdict(self)


def _amounts(draft: set[int], ref: set[int]) -> str:
    if not draft and not ref:
        return "none"
    if draft == ref:
        return "agree"
    if draft and ref and not draft & ref:
        return "conflict"
    return "draft_missing" if ref - draft else "draft_extra"


def _mentioned(text: str, catalog: list[Product]) -> set[str]:
    folded = fold(text)
    return {
        p.sku
        for p in catalog
        if p.sku.lower() in folded or (len(p.name) >= 6 and fold(p.name) in folded)
    }


def check(
    *,
    draft_text: str,
    draft_skus: set[str],
    grounded: bool,
    needs_human: bool,
    reference: str,
    known_amounts: set[int],
    catalog: list[Product],
) -> CodeChecks:
    """`known_amounts`: giá catalog + số tiền trong tri thức (để nghi câu nhân viên)."""
    a_draft, a_ref = extract_amounts(draft_text), extract_amounts(reference)
    suspect = bool(a_ref - known_amounts)
    amounts = _amounts(a_draft, a_ref)

    ref_skus = _mentioned(reference, catalog)
    products = (
        "unknown"
        if not ref_skus or not draft_skus
        else ("match" if draft_skus & ref_skus else "differ")
    )

    folded = fold(reference)
    deferred = any(p in folded for p in _DEFER) and not a_ref
    if needs_human and not deferred and (a_ref or ref_skus):
        handoff = "extra"  # bot nhờ người, nhân viên tự trả lời được ngay
    elif not needs_human and deferred:
        handoff = "missing"  # nhân viên cũng phải đi hỏi: bot tự trả lời là liều
    else:
        handoff = "agree"

    l_draft, l_ref = set(_URL.findall(draft_text)), set(_URL.findall(reference))
    links = "none" if not l_draft and not l_ref else ("match" if l_draft == l_ref else "differ")

    verdict: Verdict | None = None
    if not grounded:
        verdict = "not_sendable"
    elif not suspect and (amounts == "conflict" or products == "differ"):
        verdict = "wrong"
    return CodeChecks(amounts, products, handoff, links, suspect, verdict)
