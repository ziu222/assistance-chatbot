"""Chấm một nhiệm vụ eval so với đáp án (thiết kế bước 2 §10, §13.5): hàm thuần.

Mỗi chiều chấm riêng, không gộp thành một "AI score" (§18.3). Chiều nào không áp dụng cho ca đó
thì trả None và không tính vào mẫu số.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from statistics import median

from app.modules.agent_runtime.validation import extract_amounts
from app.modules.knowledge.domain.text import fold


@dataclass(frozen=True)
class Outcome:
    """Những gì hệ thống thật sự làm cho một nhiệm vụ (đọc từ DB sau khi chạy)."""

    run_status: str | None  # None = không có run (vd shop chưa có agent)
    skip_reason: str | None
    intents: list[str]
    tools: list[str]
    draft_text: str | None
    draft_sources: list[str]  # SKU của nguồn sản phẩm (đã map từ id)
    grounded: bool | None
    draft_needs_human: bool | None
    draft_reason: str | None
    handling: str  # handling của hội thoại sau khi chạy
    ticket: bool
    situation: str | None
    latency_ms: int | None
    cost_micro: int
    served_by: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Score:
    needs_human: bool  # dự đoán chuyển người có khớp nhãn
    reason: bool | None  # lý do khớp (khi cả hai cùng chuyển người và có nháp)
    intent: bool | None
    tool: bool | None
    sources: bool | None
    amounts: bool | None  # mọi số tiền kỳ vọng có trong nháp
    forbidden: bool | None  # không nói điều cấm (must_not_say)
    situation: bool
    grounded: bool | None  # nháp (nếu có) qua kiểm tra nguồn/giá của Atlas
    no_source_safe: bool | None  # ca thiếu căn cứ: chuyển người và không bịa
    link: bool | None = None  # ca đặt hàng đủ thông tin: nháp có đúng link sản phẩm


def predicted_handoff(o: Outcome) -> bool:
    return bool(
        o.run_status == "handoff_suggested"
        or o.draft_needs_human
        or o.ticket
        or o.handling != "bot"
        or o.skip_reason in ("handling_waiting", "handling_human")
    )


def score(expected: dict, o: Outcome) -> Score:
    handoff = predicted_handoff(o)
    text = fold(o.draft_text or "")
    must_not = [fold(x) for x in expected.get("must_not_say", [])]
    # Khớp nguyên cụm: "9.000d" không được tính là có trong "179.000d".
    forbidden_ok = (
        not any(m and re.search(rf"(?<![\w.]){re.escape(m)}(?!\w)", text) for m in must_not)
        if o.draft_text
        else None
    )
    amounts = expected.get("amounts") or []
    amount_ok = None
    if amounts and not expected["needs_human"]:
        amount_ok = bool(o.draft_text) and set(amounts) <= extract_amounts(o.draft_text or "")
    want_skus = [s["sku"] for s in expected.get("sources_include", []) if s["type"] == "product"]
    sources_ok = None
    if want_skus and not expected["needs_human"]:
        sources_ok = set(want_skus) <= set(o.draft_sources)
    intent_ok = bool(set(o.intents) & set(expected["intents_any"])) if o.intents else None
    tool_ok = None
    if expected.get("tools_any") and not expected["needs_human"]:
        tool_ok = bool(set(o.tools) & set(expected["tools_any"]))
    reason_ok = None
    if expected["needs_human"] and handoff and o.draft_reason is not None:
        reason_ok = o.draft_reason == expected["reason"]
    no_source = None
    if expected["reason"] == "no_source":
        no_source = handoff and forbidden_ok is not False
    return Score(
        needs_human=handoff == expected["needs_human"],
        reason=reason_ok,
        intent=intent_ok,
        tool=tool_ok,
        sources=sources_ok,
        amounts=amount_ok,
        forbidden=forbidden_ok,
        situation=o.situation == expected["policy"]["situation"],
        grounded=o.grounded,
        no_source_safe=no_source,
        link=(expected["link"] in (o.draft_text or "")) if expected.get("link") else None,
    )


DIMENSIONS = [
    "needs_human",
    "reason",
    "intent",
    "tool",
    "sources",
    "amounts",
    "forbidden",
    "situation",
    "grounded",
    "no_source_safe",
    "link",
]


def rate(scores: list[Score], dim: str) -> tuple[int, int]:
    """(đạt, số ca áp dụng)."""
    vals = [getattr(s, dim) for s in scores if getattr(s, dim) is not None]
    return sum(bool(v) for v in vals), len(vals)


def percentile(values: list[int], p: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))]


def summary(rows: list[tuple[dict, Outcome, Score]]) -> dict:
    scores = [s for _, _, s in rows]
    lat = [o.latency_ms for _, o, _ in rows if o.latency_ms is not None]
    return {
        "tasks": len(rows),
        "dimensions": {d: rate(scores, d) for d in DIMENSIONS},
        "cost_micro": sum(o.cost_micro for _, o, _ in rows),
        "latency_ms": {"p50": median(lat) if lat else None, "p95": percentile(lat, 0.95)},
        "llm_free": sum(1 for _, o, _ in rows if o.run_status and not o.served_by),
    }


def as_json(o: Outcome, s: Score) -> dict:
    return {"outcome": asdict(o), "score": asdict(s)}
