"""Cổng eval hồi quy và bộ ca chuẩn Autopilot (thiết kế 05 L4, thiết kế 06 A5): hàm thuần.

- Bản từ Learning: chạy ca bài học mới + ca cũ còn hiệu lực + bộ ca chuẩn cho mọi ý định đang
  Autopilot. Hỏng bất kỳ ca nào ⇒ chặn phát hành (`eval_failed`).
- Bản chủ shop sửa tay: vẫn phát hành (chủ shop là nguồn sự thật), rồi chạy bộ ca chuẩn cho các ý
  định đang Autopilot; ý định nào hỏng bị hạ cấp với lý do `eval_regressed`.
- Ca chuẩn kiểm HÀNH VI (có nguồn hợp lệ, không bịa số tiền, không chuyển người thừa). Ca nào cần
  kiểm số tiền cụ thể bắt buộc gắn `entry_id` + `entry_hash` để chủ shop đổi phí ship/giá không
  gây hạ cấp oan.
"""

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from html import escape
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.knowledge.domain.releases import (
    KnowledgeSnapshot,
    SnapshotItem,
    build_snapshot,
    entry_hash,
    find_item,
)
from app.modules.orders.domain.order_flow import claims_order_placed
from app.shared.domain.text import extract_amounts, fold

EVAL_SYSTEM = """Bạn là nhân viên CSKH trả lời câu hỏi của khách CHỈ dựa trên khối `<knowledge>`.
Quy tắc:
- Trả lời ngắn gọn, đúng số tiền, thời hạn và thông tin có trong `<knowledge>`.
- Không tự bịa thông tin không có trong `<knowledge>`.
"""

STANDARD_EVAL_SYSTEM = """Bạn là nhân viên CSKH trả lời khách CHỈ dựa trên khối `<knowledge>` và
khối `<catalog>` (danh sách sản phẩm đang bán, nếu có).
Quy tắc bắt buộc:
- Trả lời ngắn gọn, tự nhiên bằng tiếng Việt, chỉ dùng thông tin có trong hai khối trên.
- Trích đúng nguồn (`sources`: `type` và `id`) từ `<faq id="...">`, `<doc id="...">` hoặc
  `<product id="...">` (type `product`).
- Không tự bịa số tiền, thời hạn, hay đường link không có trong hai khối trên.
- Chỉ đặt `needs_human = true` khi thật sự thiếu thông tin hoặc cần nhân viên xử lý; không chuyển
  người thừa khi câu hỏi là lời chào thông thường hoặc đã có câu trả lời trong `<knowledge>`.
"""

MAX_EVAL_REPLY = 1200
_HTML = re.compile(r"<\s*/?\s*[a-z!]", re.IGNORECASE)
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
_URL_TAIL = ".,;:!?)]}…"

EvalSourceKind = Literal["lesson", "manual", "standard"]


class EvalSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["faq", "doc", "product"]
    id: str = Field(max_length=64)


class EvalDraftOut(BaseModel):
    """Đầu ra có cấu trúc của ca chuẩn để kiểm hành vi (nguồn, số tiền, không chuyển người thừa)."""

    model_config = ConfigDict(extra="forbid")
    reply: str = Field(max_length=1200)
    sources: list[EvalSource] = Field(default_factory=list, max_length=10)
    needs_human: bool = False
    reason: Literal["", "no_source", "policy", "discount", "order", "complaint", "unclear"] = ""


@dataclass(frozen=True)
class EvalCaseSpec:
    case_id: str
    question: str
    must_include_amounts: tuple[int, ...] = ()
    must_include_terms: tuple[str, ...] = ()
    forbidden_amounts: tuple[int, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    is_new: bool = False
    entry_id: str | None = None  # mục tri thức ca này kiểm tra (None: ca hành vi chung / ca cũ)
    entry_hash: str | None = None  # vân tay nội dung mục đó lúc tạo ca
    source: EvalSourceKind = "lesson"
    intent: str | None = None
    check_behavior: bool = False
    require_sources: bool = False
    allow_needs_human: bool = False

    def expected_dict(self) -> dict:
        data: dict = {
            "must_include_amounts": list(self.must_include_amounts),
            "must_include_terms": list(self.must_include_terms),
            "forbidden_amounts": list(self.forbidden_amounts),
            "forbidden_terms": list(self.forbidden_terms),
        }
        if self.check_behavior:
            data["check_behavior"] = True
            data["require_sources"] = self.require_sources
            data["allow_needs_human"] = self.allow_needs_human
        return data

    @classmethod
    def from_row(
        cls,
        case_id: str,
        question: str,
        expected: dict,
        entry_id: str | None = None,
        entry_hash: str | None = None,
        *,
        source: EvalSourceKind = "lesson",
        intent: str | None = None,
    ) -> "EvalCaseSpec":
        check_behavior = bool(expected.get("check_behavior", source == "standard"))
        return cls(
            entry_id=entry_id,
            entry_hash=entry_hash,
            case_id=case_id,
            question=question,
            must_include_amounts=tuple(int(x) for x in expected.get("must_include_amounts", ())),
            must_include_terms=tuple(str(x) for x in expected.get("must_include_terms", ())),
            forbidden_amounts=tuple(int(x) for x in expected.get("forbidden_amounts", ())),
            forbidden_terms=tuple(str(x) for x in expected.get("forbidden_terms", ())),
            is_new=False,
            source=source,
            intent=intent,
            check_behavior=check_behavior,
            require_sources=bool(expected.get("require_sources", False)),
            allow_needs_human=bool(expected.get("allow_needs_human", False)),
        )


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    question: str
    reply: str
    passed: bool
    reasons: tuple[str, ...] = ()
    is_new: bool = False
    intent: str | None = None
    source: str = "lesson"


@dataclass(frozen=True)
class GateDecision:
    passed: bool
    total: int
    failed_cases: tuple[CaseOutcome, ...] = ()

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "total": self.total,
            "failed_cases": [asdict(c) for c in self.failed_cases],
        }


def build_lesson_case(
    proposal: dict,
    *,
    kind: str,
    case_id: str,
    old_answer: str | None = None,
    intent: str | None = None,
) -> EvalCaseSpec:
    """Tạo ca hồi quy cho bài học mới từ nội dung đề xuất (và giá cũ cần cấm nếu `faq_update`)."""
    question = str(proposal.get("question") or proposal.get("title") or "").strip()
    answer = str(proposal.get("answer") or proposal.get("body") or "").strip()
    new_amounts = extract_amounts(answer)
    old_amounts = extract_amounts(old_answer) if (kind == "faq_update" and old_answer) else set()
    forbidden_amounts = tuple(sorted(old_amounts - new_amounts))
    return EvalCaseSpec(
        case_id=case_id,
        question=question,
        must_include_amounts=tuple(sorted(new_amounts)),
        forbidden_amounts=forbidden_amounts,
        is_new=True,
        source="lesson",
        intent=intent,
    )


def build_standard_amount_case(
    intent: str,
    item: SnapshotItem,
    *,
    case_id: str,
) -> EvalCaseSpec:
    """Tạo ca chuẩn có kiểm số tiền gắn chặt `entry_id` + `entry_hash`.

    Khi chủ shop đổi nội dung mục (ví dụ đổi phí ship), vân tay `entry_hash` đổi theo ⇒ ca tự hết
    hiệu lực (`still_valid` trả False), không gây hạ cấp oan.
    """
    return EvalCaseSpec(
        case_id=case_id,
        question=item.first.strip(),
        must_include_amounts=tuple(sorted(extract_amounts(item.second))),
        entry_id=item.id,
        entry_hash=entry_hash(item),
        source="standard",
        intent=intent,
        check_behavior=True,
        require_sources=True,
        allow_needs_human=False,
    )


def build_standard_cases(
    intents: Sequence[str],
    snapshot: KnowledgeSnapshot,
    *,
    catalog_names: Sequence[str] = (),
    covered_entry_ids: frozenset[str] = frozenset(),
    max_faq_probes: int = 3,
) -> list[EvalCaseSpec]:
    """Dựng bộ ca chuẩn kiểm HÀNH VI theo từng ý định đang Autopilot (thiết kế 06 A5).

    Không khóa cứng con số tiền vào ca động: chỉ kiểm có nguồn hợp lệ, không bịa số, không chuyển
    người thừa. Chủ shop đổi phí ship/giá ⇒ vẫn đạt nếu bot trả lời đúng theo tri thức mới.
    Bỏ qua `covered_entry_ids` (các mục đã có ca chuẩn lưu sẵn còn hiệu lực) để không gọi LLM trùng.
    """
    specs: list[EvalCaseSpec] = []
    seen = sorted(set(intents))
    sorted_catalog = sorted({n.strip() for n in catalog_names if n and n.strip()})

    for intent in seen:
        if intent == "greeting":
            specs.extend(
                [
                    EvalCaseSpec(
                        case_id="std:greeting:1",
                        question="Xin chào shop!",
                        source="standard",
                        intent="greeting",
                        check_behavior=True,
                        require_sources=False,
                        allow_needs_human=False,
                    ),
                    EvalCaseSpec(
                        case_id="std:greeting:2",
                        question="Shop ơi cho mình hỏi chút ạ",
                        source="standard",
                        intent="greeting",
                        check_behavior=True,
                        require_sources=False,
                        allow_needs_human=False,
                    ),
                ]
            )
        elif intent == "faq":
            if snapshot.faqs:
                for faq in snapshot.faqs[:max_faq_probes]:
                    if faq.id in covered_entry_ids:
                        continue
                    specs.append(
                        EvalCaseSpec(
                            case_id=f"std:faq:{faq.id}",
                            question=faq.first.strip(),
                            entry_id=faq.id,
                            source="standard",
                            intent="faq",
                            check_behavior=True,
                            require_sources=True,
                            allow_needs_human=False,
                        )
                    )
            elif snapshot.docs:
                for doc in snapshot.docs[: min(2, max_faq_probes)]:
                    if doc.id in covered_entry_ids:
                        continue
                    specs.append(
                        EvalCaseSpec(
                            case_id=f"std:faq:{doc.id}",
                            question=f"Cho mình hỏi về {doc.first.strip()}?",
                            entry_id=doc.id,
                            source="standard",
                            intent="faq",
                            check_behavior=True,
                            require_sources=True,
                            allow_needs_human=False,
                        )
                    )
            else:
                specs.append(
                    EvalCaseSpec(
                        case_id="std:faq:empty",
                        question="Shop mở cửa lúc mấy giờ ạ?",
                        source="standard",
                        intent="faq",
                        check_behavior=True,
                        require_sources=False,
                        allow_needs_human=True,
                    )
                )
        elif intent == "product_info":
            if sorted_catalog:
                for idx, name in enumerate(sorted_catalog[:2], start=1):
                    specs.append(
                        EvalCaseSpec(
                            case_id=f"std:product_info:{idx}",
                            question=f"Cho mình hỏi thông tin về sản phẩm {name}?",
                            source="standard",
                            intent="product_info",
                            check_behavior=True,
                            require_sources=False,
                            allow_needs_human=False,
                        )
                    )
            else:
                specs.append(
                    EvalCaseSpec(
                        case_id="std:product_info:1",
                        question="Shop tư vấn giúp mình thông tin sản phẩm với ạ?",
                        source="standard",
                        intent="product_info",
                        check_behavior=True,
                        require_sources=False,
                        allow_needs_human=False,
                    )
                )
        elif intent == "price_quote":
            if sorted_catalog:
                for idx, name in enumerate(sorted_catalog[:2], start=1):
                    specs.append(
                        EvalCaseSpec(
                            case_id=f"std:price_quote:{idx}",
                            question=f"Sản phẩm {name} giá bao nhiêu ạ?",
                            source="standard",
                            intent="price_quote",
                            check_behavior=True,
                            require_sources=False,
                            allow_needs_human=False,
                        )
                    )
            else:
                specs.append(
                    EvalCaseSpec(
                        case_id="std:price_quote:1",
                        question="Cho mình hỏi giá sản phẩm bên shop với ạ?",
                        source="standard",
                        intent="price_quote",
                        check_behavior=True,
                        require_sources=False,
                        allow_needs_human=False,
                    )
                )
    return specs


def _check_expectations(spec: EvalCaseSpec, text: str) -> list[str]:
    reasons: list[str] = []
    found_amounts = extract_amounts(text)
    folded = fold(text)
    for amt in spec.must_include_amounts:
        if amt not in found_amounts:
            reasons.append(f"missing_amount:{amt}")
    for term in spec.must_include_terms:
        if fold(term) not in folded:
            reasons.append(f"missing_term:{term}")
    for amt in spec.forbidden_amounts:
        if amt in found_amounts:
            reasons.append(f"forbidden_amount:{amt}")
    for term in spec.forbidden_terms:
        if fold(term) in folded:
            reasons.append(f"forbidden_term:{term}")
    return reasons


def grade_reply(spec: EvalCaseSpec, reply: str) -> CaseOutcome:
    """Chấm câu trả lời thử nghiệm dạng chữ thuần (ca bài học) bằng quy tắc xác định."""
    text = reply.strip()
    reasons: list[str] = ["empty_reply"] if not text else _check_expectations(spec, text)
    return CaseOutcome(
        case_id=spec.case_id,
        question=spec.question,
        reply=text,
        passed=not reasons,
        reasons=tuple(reasons),
        is_new=spec.is_new,
        intent=spec.intent,
        source=spec.source,
    )


def grade_standard_reply(
    spec: EvalCaseSpec,
    draft: EvalDraftOut,
    *,
    valid_sources: set[tuple[str, str]] | frozenset[tuple[str, str]],
    known_amounts: set[int] | frozenset[int],
    known_urls: set[str] | frozenset[str] = frozenset(),
) -> CaseOutcome:
    """Chấm ca chuẩn kiểm HÀNH VI: có nguồn hợp lệ, không bịa số, không chuyển người thừa."""
    text = draft.reply.strip()
    reasons: list[str] = []
    if not text:
        reasons.append("empty_reply")
    elif len(text) > MAX_EVAL_REPLY:
        reasons.append("length")

    if any((s.type, s.id) not in valid_sources for s in draft.sources):
        reasons.append("source_not_provided")
    if spec.require_sources and not draft.sources:
        reasons.append("missing_source")

    if text:
        unknown_amounts = sorted(extract_amounts(text) - set(known_amounts))
        for amt in unknown_amounts:
            reasons.append(f"ungrounded_amount:{amt}")
        urls = [u.rstrip(_URL_TAIL) for u in _URL.findall(text)]
        if _HTML.search(text) or any(u not in known_urls for u in urls):
            reasons.append("url_or_html")
        if claims_order_placed(text):
            reasons.append("order_claim")
        reasons.extend(_check_expectations(spec, text))

    if not spec.allow_needs_human and draft.needs_human:
        reasons.append("unnecessary_handoff")

    return CaseOutcome(
        case_id=spec.case_id,
        question=spec.question,
        reply=text,
        passed=not reasons,
        reasons=tuple(reasons),
        is_new=spec.is_new,
        intent=spec.intent,
        source=spec.source,
    )


def evaluate_gate(outcomes: Sequence[CaseOutcome]) -> GateDecision:
    """Điều kiện đạt cổng eval (§6.4 bước 4): ca mới đúng, không ca cũ/ca chuẩn nào hỏng."""
    failed = tuple(o for o in outcomes if not o.passed)
    return GateDecision(
        passed=len(failed) == 0 and len(outcomes) > 0, total=len(outcomes), failed_cases=failed
    )


def select_regression_cases(
    cases: Sequence[EvalCaseSpec], *, intent: str | None, limit: int
) -> list[EvalCaseSpec]:
    """Chọn ca bài học cũ để chạy lại: cùng ý định trước, rồi mới nhất trước (`cases` theo thứ tự
    tạo, cũ trước). Giữ số lời gọi LLM mỗi bài học có giới hạn khi số bài học tăng."""
    newest_first = list(reversed(cases))
    same = [c for c in newest_first if intent is not None and c.intent == intent]
    rest = [c for c in newest_first if c not in same]
    return (same + rest)[:limit]


def regressed_intents(outcomes: Sequence[CaseOutcome]) -> tuple[str, ...]:
    """Các ý định có ít nhất một ca chuẩn bị hỏng ⇒ cần hạ cấp với `eval_regressed`."""
    return tuple(sorted({o.intent for o in outcomes if not o.passed and o.intent}))


def still_valid(spec: EvalCaseSpec, snapshot: KnowledgeSnapshot) -> bool:
    """Ca hồi quy / ca chuẩn còn hiệu lực trên `snapshot` không.

    - Ca gắn `entry_id` + `entry_hash`: chỉ còn hiệu lực khi mục còn tồn tại và đúng vân tay nội
      dung lúc tạo ca. Chủ shop đổi phí ship/giá hay xóa mục ⇒ ca hết hiệu lực, không chặn/hạ cấp
      oan.
    - Ca chuẩn có kiểm số tiền (`must_include_amounts` / `forbidden_amounts`) BẮT BUỘC phải gắn
      `entry_id` + `entry_hash`; thiếu thì bỏ qua để không bao giờ gây hạ cấp oan.
    - Ca gắn `entry_id` không có hash (vd ca hành vi hỏi mục FAQ): còn hiệu lực khi mục tồn tại.
    """
    if spec.entry_id is not None and spec.entry_hash is not None:
        item = find_item(snapshot, spec.entry_id)
        return item is not None and entry_hash(item) == spec.entry_hash
    if spec.source == "standard" and (spec.must_include_amounts or spec.forbidden_amounts):
        return False
    if spec.entry_id is not None:
        return find_item(snapshot, spec.entry_id) is not None
    return True


def apply_candidate_to_snapshot(
    snapshot: KnowledgeSnapshot,
    *,
    kind: str,
    proposal: dict,
    replaces: str | None,
    item_id: str,
    topic: str | None = None,
) -> KnowledgeSnapshot:
    """Dựng ảnh chụp nháp phát hành = bản đang hiệu lực + thay đổi của ứng viên."""
    topics = (topic,) if topic and topic != "other" else None
    if kind == "doc_section":
        docs = [d for d in snapshot.docs if d.id != (replaces or item_id)]
        docs.append(
            SnapshotItem(
                id=replaces or item_id,
                first=str(proposal.get("title") or proposal.get("question") or ""),
                second=str(proposal.get("body") or proposal.get("answer") or ""),
                topics=topics,
            )
        )
        return build_snapshot(snapshot.faqs, docs)

    target_id = replaces or item_id
    faqs = [f for f in snapshot.faqs if f.id != target_id]
    faqs.append(
        SnapshotItem(
            id=target_id,
            first=str(proposal.get("question") or proposal.get("title") or ""),
            second=str(proposal.get("answer") or proposal.get("body") or ""),
            topics=topics,
        )
    )
    return build_snapshot(faqs, snapshot.docs)


def eval_user_turn(knowledge_block: str, question: str) -> str:
    return f"{knowledge_block}\n<question>{escape(question, quote=False)}</question>"
