"""Hàm chấm của eval runner (bước 2.12)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "evals"))

from scoring import Outcome, rate, score, summary  # noqa: E402


def outcome(**over) -> Outcome:
    base = dict(
        run_status="completed",
        skip_reason=None,
        intents=["price_quote"],
        tools=["search_products"],
        draft_text="Dạ áo giá 350.000đ ạ",
        draft_sources=["AK-02"],
        grounded=True,
        draft_needs_human=False,
        draft_reason="",
        handling="bot",
        ticket=False,
        situation=None,
        latency_ms=1200,
        cost_micro=900,
        served_by=["openai:gpt-4.1"],
    )
    return Outcome(**(base | over))


PRICE = {
    "intents_any": ["price_quote"],
    "tools_any": ["search_products"],
    "sources_include": [{"type": "product", "sku": "AK-02"}],
    "amounts": [350000],
    "must_not_say": ["1.000đ"],
    "needs_human": False,
    "reason": "",
    "policy": {"situation": None},
}


def test_correct_price_answer_passes_every_dimension():
    s = score(PRICE, outcome())
    assert (s.needs_human, s.intent, s.tool, s.sources, s.amounts, s.forbidden) == (
        True,
        True,
        True,
        True,
        True,
        True,
    )
    assert s.reason is None and s.no_source_safe is None


def test_wrong_amount_and_forbidden_value_fail():
    s = score(PRICE, outcome(draft_text="Dạ giá chỉ 1.000đ thôi ạ", grounded=False))
    assert s.amounts is False and s.forbidden is False and s.grounded is False


def test_no_source_case_needs_a_handoff_without_invented_values():
    exp = {
        "intents_any": ["faq"],
        "needs_human": True,
        "reason": "no_source",
        "must_not_say": ["30.000đ"],
        "policy": {"situation": None},
    }
    safe = score(
        exp,
        outcome(
            draft_text="Dạ em nhờ nhân viên báo phí ship ạ",
            draft_needs_human=True,
            draft_reason="no_source",
        ),
    )
    assert safe.needs_human and safe.no_source_safe and safe.reason
    invented = score(exp, outcome(draft_text="Phí ship 30.000đ ạ", draft_needs_human=False))
    assert invented.needs_human is False and invented.no_source_safe is False


def test_handoff_detected_from_rule_ticket_or_handling():
    exp = {
        "intents_any": ["human_request"],
        "needs_human": True,
        "reason": "unclear",
        "policy": {"situation": None},
    }
    for o in (
        outcome(run_status="handoff_suggested", draft_text=None),
        outcome(ticket=True, draft_text=None),
        outcome(skip_reason="handling_waiting", run_status="skipped", draft_text=None),
    ):
        assert score(exp, o).needs_human


def test_rates_skip_dimensions_that_do_not_apply():
    rows = [
        (PRICE, outcome(), score(PRICE, outcome())),
        (PRICE, outcome(draft_text=None), score(PRICE, outcome(draft_text=None))),
    ]
    assert rate([r[2] for r in rows], "forbidden") == (1, 1)
    report = summary(rows)
    assert report["tasks"] == 2 and report["dimensions"]["amounts"] == (1, 2)
    assert report["latency_ms"]["p50"] == 1200
