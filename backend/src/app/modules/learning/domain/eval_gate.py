"""Cổng eval hồi quy trước khi phát hành bài học (thiết kế 05 L4, §6.4): hàm thuần.

Không có gì tự áp dụng: sau khi chủ shop duyệt, bài học được ghép thành bản nháp phát hành và
chạy qua ca hồi quy mới + các ca hồi quy cũ của tenant. Đạt mới phát hành; hỏng trả về ca lỗi.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from html import escape

from app.modules.knowledge.domain.releases import KnowledgeSnapshot, SnapshotItem, build_snapshot
from app.shared.domain.text import extract_amounts, fold

EVAL_SYSTEM = """Bạn là nhân viên CSKH trả lời câu hỏi của khách CHỈ dựa trên khối `<knowledge>`.
Quy tắc:
- Trả lời ngắn gọn, đúng số tiền, thời hạn và thông tin có trong `<knowledge>`.
- Không tự bịa thông tin không có trong `<knowledge>`.
"""


@dataclass(frozen=True)
class EvalCaseSpec:
    case_id: str
    question: str
    must_include_amounts: tuple[int, ...] = ()
    must_include_terms: tuple[str, ...] = ()
    forbidden_amounts: tuple[int, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    is_new: bool = False

    def expected_dict(self) -> dict:
        return {
            "must_include_amounts": list(self.must_include_amounts),
            "must_include_terms": list(self.must_include_terms),
            "forbidden_amounts": list(self.forbidden_amounts),
            "forbidden_terms": list(self.forbidden_terms),
        }

    @classmethod
    def from_row(cls, case_id: str, question: str, expected: dict) -> "EvalCaseSpec":
        return cls(
            case_id=case_id,
            question=question,
            must_include_amounts=tuple(int(x) for x in expected.get("must_include_amounts", ())),
            must_include_terms=tuple(str(x) for x in expected.get("must_include_terms", ())),
            forbidden_amounts=tuple(int(x) for x in expected.get("forbidden_amounts", ())),
            forbidden_terms=tuple(str(x) for x in expected.get("forbidden_terms", ())),
            is_new=False,
        )


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    question: str
    reply: str
    passed: bool
    reasons: tuple[str, ...] = ()
    is_new: bool = False


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
    )


def grade_reply(spec: EvalCaseSpec, reply: str) -> CaseOutcome:
    """Chấm câu trả lời thử nghiệm bằng quy tắc xác định."""
    text = reply.strip()
    reasons: list[str] = []
    if not text:
        reasons.append("empty_reply")
    else:
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
    return CaseOutcome(
        case_id=spec.case_id,
        question=spec.question,
        reply=text,
        passed=not reasons,
        reasons=tuple(reasons),
        is_new=spec.is_new,
    )


def evaluate_gate(outcomes: Sequence[CaseOutcome]) -> GateDecision:
    """Điều kiện đạt cổng eval (§6.4 bước 4): ca mới đúng, không ca cũ nào hỏng, không điều cấm."""
    failed = tuple(o for o in outcomes if not o.passed)
    return GateDecision(
        passed=len(failed) == 0 and len(outcomes) > 0, total=len(outcomes), failed_cases=failed
    )


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
