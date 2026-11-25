"""Quy tắc dựng và kiểm tra ứng viên tri thức từ bản ghi sửa của nhân viên (thiết kế 05 L3).

Hàm thuần, không I/O. Thực thi các bất biến 2, 5, 6:
- Ứng viên chỉ dựng từ chữ nhân viên đã gửi, nhãn ý định/chủ đề và tri thức shop — KHÔNG chữ khách.
- Số tiền, thời hạn, giờ, link trong ứng viên phải có trong chữ nhân viên làm bằng chứng.
- Dữ liệu cá nhân (SĐT, email, mã đơn, địa chỉ cụ thể) bị lọc và chặn duyệt nếu còn sót.
- Chặn chuỗi dài hơn 8 từ trùng với chữ khách trong các hội thoại làm bằng chứng.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from html import escape
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.shared.domain.text import extract_amounts, fold

CandidateKind = Literal["faq_new", "faq_update", "doc_section"]

MIN_EVIDENCE = 2
MIN_DISCARDED_CHARS = 15
CUSTOMER_OVERLAP_WORDS = 8  # dài hơn 8 từ (tức >= 9 từ liên tiếp) trùng chữ khách ⇒ chặn

_DURATION = re.compile(r"\b(\d+)\s*(ngay|tuan|thang|nam|gio|tieng|phut|h)\b")
_UNIT = {"tieng": "gio", "h": "gio"}
_CLOCK = re.compile(r"\b(\d{1,2})\s*(?::|h)\s*(\d{2})?\b")
_LINK = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
_LINK_TAIL = ".,;:!?)]}…"

# Dữ liệu cá nhân cần loại khỏi ứng viên tri thức (bất biến 6).
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("phone", re.compile(r"(?<!\d)(?:\+84|84|0)\d(?:[\s.-]?\d){8,9}(?!\d)")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("order_code", re.compile(r"\b(?:DH|ORD|MDH)[-_]?\d{4,}\b|#\d{5,}\b", re.IGNORECASE)),
    (
        "address",
        re.compile(
            r"\b(?:số\s*\d+[a-z0-9/]*|ngõ\s*\d+|hẻm\s*\d+)\s+[^,.\n]{3,40}"
            r"(?:đường|phố|phường|quận|huyện|tp\.?|tỉnh)[^,.\n]{0,30}",
            re.IGNORECASE,
        ),
    ),
)

SYSTEM = """Bạn là bộ tổng hợp tri thức CSKH cho chủ shop.
Chỉ sử dụng thông tin có trong các câu trả lời đã gửi của NHÂN VIÊN và FAQ hiện có của shop.
Quy tắc bắt buộc:
- Không bịa thêm số tiền, thời hạn, giờ làm việc hay đường link không có trong câu nhân viên.
- Không đưa số điện thoại khách, địa chỉ riêng, mã đơn hàng, hay tên người cụ thể vào nội dung.
- Nếu câu nhân viên sửa/cập nhật thông tin mâu thuẫn với một FAQ hiện có, chọn `faq_update` và ghi
  đúng `replaces_id` của FAQ đó.
- Nếu thông tin chưa có trong FAQ, chọn `faq_new` (ngắn gọn) hoặc `doc_section` (chính sách dài).
- Viết câu hỏi/tiêu đề tổng quát từ chủ đề, giọng tự nhiên tiếng Việt, không chép nguyên văn.
"""


class CandidateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: CandidateKind
    title: str = Field(min_length=3, max_length=300)  # câu hỏi (faq_*) hoặc tiêu đề (doc_section)
    body: str = Field(min_length=5, max_length=4000)  # câu trả lời (faq_*) hoặc nội dung tài liệu
    replaces_id: str | None = None


@dataclass(frozen=True)
class EvidenceItem:
    draft_id: str
    conversation_id: str
    message_id: str | None
    intent: str
    topic: str
    action: str  # edited | discarded | as_is
    diff_features: dict
    staff_text: str


@dataclass(frozen=True)
class CandidateChecks:
    passed: bool
    ungrounded_facts: dict[str, list[str]] = field(default_factory=dict)
    pii: list[str] = field(default_factory=list)
    customer_overlap: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def is_significant(item: EvidenceItem) -> bool:
    """Lọc bằng code (§6.2 bước 1): chỉ giữ sửa đáng kể hoặc bỏ mà nhân viên trả lời có nội dung."""
    text = item.staff_text.strip()
    if not text:
        return False
    if item.action == "edited":
        df = item.diff_features or {}
        return bool(
            df.get("amounts_added")
            or df.get("amounts_removed")
            or df.get("links_changed")
            or (df.get("sentences_added") or 0) >= 1
        )
    if item.action == "discarded":
        return len(text) >= MIN_DISCARDED_CHARS
    return False


def has_enough_evidence(items: Sequence[EvidenceItem], *, min_count: int = MIN_EVIDENCE) -> bool:
    """Đủ bằng chứng (§6.2 bước 2): ít nhất 2 lần sửa đáng kể cùng hướng."""
    sig = [i for i in items if is_significant(i)]
    if len(sig) < min_count:
        return False
    # Nếu tất cả bằng chứng đều chỉ đổi số tiền, đòi hỏi cùng một con số mới lặp lại >= min_count.
    added_sets = [set(i.diff_features.get("amounts_added") or ()) for i in sig]
    if all(added_sets) and not any(
        sum(1 for s in added_sets if amt in s) >= min_count for amt in set().union(*added_sets)
    ):
        return False
    return True


def detect_pii(text: str) -> list[str]:
    """Phát hiện loại dữ liệu cá nhân còn xuất hiện trong văn bản."""
    found: list[str] = []
    for label, pattern in _PII_PATTERNS:
        if pattern.search(text):
            found.append(label)
    return found


def strip_pii(text: str) -> tuple[str, list[str]]:
    """Loại bỏ dữ liệu cá nhân khỏi văn bản ứng viên trước khi kiểm tra và trình chủ shop."""
    cleaned = text
    removed: list[str] = []
    for label, pattern in _PII_PATTERNS:
        if pattern.search(cleaned):
            removed.append(label)
            cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, removed


def _extract_facts(text: str) -> dict[str, set[str]]:
    folded = fold(text)
    durations = {f"{n} {_UNIT.get(u, u)}" for n, u in _DURATION.findall(folded)}
    clocks = {f"{int(h)}:{m or '00'}" for h, m in _CLOCK.findall(folded) if int(h) <= 23}
    links = {u.rstrip(_LINK_TAIL).lower() for u in _LINK.findall(text)}
    return {
        "amounts": {str(a) for a in extract_amounts(text)},
        "durations": durations,
        "clocks": clocks,
        "links": links,
    }


def has_customer_overlap(
    candidate_text: str,
    customer_texts: Sequence[str],
    *,
    max_words: int = CUSTOMER_OVERLAP_WORDS,
) -> bool:
    """Chặn chuỗi dài hơn `max_words` từ liên tiếp trùng với chữ của khách (bất biến 2)."""
    n = max_words + 1
    cand_words = fold(candidate_text).split()
    if len(cand_words) < n:
        return False
    cand_ngrams = {tuple(cand_words[i : i + n]) for i in range(len(cand_words) - n + 1)}
    for raw in customer_texts:
        words = fold(raw).split()
        if len(words) < n:
            continue
        for i in range(len(words) - n + 1):
            if tuple(words[i : i + n]) in cand_ngrams:
                return True
    return False


def check_candidate(
    proposal_text: str,
    *,
    staff_texts: Sequence[str],
    customer_texts: Sequence[str],
) -> CandidateChecks:
    """Kiểm tra ứng viên bằng code (§6.2 bước 4): sự thật, PII, và không trùng chữ khách."""
    evidence_facts: dict[str, set[str]] = {
        "amounts": set(),
        "durations": set(),
        "clocks": set(),
        "links": set(),
    }
    for st in staff_texts:
        f = _extract_facts(st)
        for k in evidence_facts:
            evidence_facts[k] |= f[k]

    cand_facts = _extract_facts(proposal_text)
    ungrounded = {
        k: sorted(cand_facts[k] - evidence_facts[k])
        for k in evidence_facts
        if cand_facts[k] - evidence_facts[k]
    }
    pii = detect_pii(proposal_text)
    overlap = has_customer_overlap(proposal_text, customer_texts)
    return CandidateChecks(
        passed=not ungrounded and not pii and not overlap,
        ungrounded_facts=ungrounded,
        pii=pii,
        customer_overlap=overlap,
    )


def fingerprint(kind: str, intent: str, topic: str, text: str) -> str:
    """Vân tay nội dung để tránh đề xuất lặp lại ứng viên giống nhau trong 30 ngày."""
    raw = f"{kind}|{intent}|{topic}|{fold(text)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def user_turn(
    intent: str,
    topic: str,
    staff_texts: Sequence[str],
    existing_faqs: Sequence[tuple[str, str, str]],
) -> str:
    """Dựng đầu vào cho route `learning`: CHỈ chữ nhân viên, nhãn ý định/chủ đề và FAQ hiện có."""
    faq_lines = "\n".join(
        f'<faq id="{escape(fid)}">\n'
        f"Q: {escape(q, quote=False)}\nA: {escape(a, quote=False)}\n</faq>"
        for fid, q, a in existing_faqs
    )
    staff_lines = "\n".join(f"<reply>{escape(t, quote=False)}</reply>" for t in staff_texts)
    return (
        f'<group intent="{escape(intent)}" topic="{escape(topic)}">\n'
        f"<existing_faqs>\n{faq_lines}\n</existing_faqs>\n"
        f"<staff_replies>\n{staff_lines}\n</staff_replies>\n"
        "</group>"
    )
