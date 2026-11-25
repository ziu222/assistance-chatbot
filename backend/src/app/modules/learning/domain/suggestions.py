"""Quy tắc đề xuất `brand_example` và `order_flow` (thiết kế 05 L6 & 03 O6): hàm thuần.

- `brand_example`: câu nháp được nhân viên dùng nguyên văn (`as_is`) nhiều lần, không chứa PII,
  gợi ý chủ shop thêm vào `BrandProfile.samples` (chủ shop chọn, không tự áp dụng).
- `order_flow`: nhân viên thường tự điền/sửa thêm một trường khi xác nhận đơn (`order_confirmed`),
  gợi ý thêm trường đó vào `OrderFlowConfig.required_fields`.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from app.modules.learning.domain.candidates import detect_pii, strip_pii
from app.modules.orders.domain.order_flow import FIELDS, LABELS
from app.shared.domain.text import fold

MIN_BRAND_USES = 3
MIN_BRAND_CHARS = 15
MAX_BRAND_CHARS = 300
MIN_ORDER_FIELD_EDITS = 3


@dataclass(frozen=True)
class BrandUseItem:
    draft_id: str
    conversation_id: str
    message_id: str | None
    intent: str
    staff_text: str


@dataclass(frozen=True)
class BrandCandidateSpec:
    intent: str
    sample: str
    evidence: tuple[dict, ...]


@dataclass(frozen=True)
class OrderSignalItem:
    conversation_id: str
    order_request_id: str
    staff_edited: tuple[str, ...]


@dataclass(frozen=True)
class OrderFlowCandidateSpec:
    field: str
    label: str
    count: int
    evidence: tuple[dict, ...]


def pick_brand_examples(
    items: Sequence[BrandUseItem],
    *,
    existing_samples: Sequence[str],
    min_uses: int = MIN_BRAND_USES,
) -> list[BrandCandidateSpec]:
    """Chọn câu `as_is` lặp lại >= `min_uses` lần, sạch PII và chưa có trong BrandProfile."""
    existing = {fold(s) for s in existing_samples}
    groups: dict[str, list[tuple[BrandUseItem, str]]] = defaultdict(list)
    for item in items:
        cleaned, _ = strip_pii(item.staff_text)
        if not (MIN_BRAND_CHARS <= len(cleaned) <= MAX_BRAND_CHARS):
            continue
        if detect_pii(cleaned):
            continue
        key = fold(cleaned)
        if not key or key in existing:
            continue
        groups[key].append((item, cleaned))

    out: list[BrandCandidateSpec] = []
    for _key, entries in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(entries) < min_uses:
            continue
        first_item, sample = entries[0]
        evidence = tuple(
            {
                "draft_id": it.draft_id,
                "conversation_id": it.conversation_id,
                "message_id": it.message_id,
            }
            for it, _ in entries
        )
        out.append(BrandCandidateSpec(intent=first_item.intent, sample=sample, evidence=evidence))
    return out


def suggest_order_flow_fields(
    signals: Sequence[OrderSignalItem],
    *,
    current_required: Sequence[str],
    min_count: int = MIN_ORDER_FIELD_EDITS,
) -> list[OrderFlowCandidateSpec]:
    """Đề xuất thêm trường vào quy trình đặt hàng khi nhân viên hay điền thêm lúc xác nhận."""
    already = set(current_required)
    valid = set(FIELDS)
    by_field: dict[str, list[OrderSignalItem]] = defaultdict(list)
    for sig in signals:
        for f in set(sig.staff_edited):
            if f in valid and f not in already:
                by_field[f].append(sig)

    out: list[OrderFlowCandidateSpec] = []
    for f in FIELDS:
        hits = by_field.get(f, [])
        if len(hits) < min_count:
            continue
        evidence = tuple(
            {
                "conversation_id": h.conversation_id,
                "order_request_id": h.order_request_id,
            }
            for h in hits
        )
        out.append(
            OrderFlowCandidateSpec(field=f, label=LABELS[f], count=len(hits), evidence=evidence)
        )
    return out
