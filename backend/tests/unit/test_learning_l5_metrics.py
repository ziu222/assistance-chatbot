from app.modules.shadow.domain.readiness import (
    IntentStats,
    classify_shown_use,
    intent_priority_score,
    readiness,
)


def test_classify_shown_use_rules():
    assert classify_shown_use("as_is", 0.0, {}).sendable is True
    assert classify_shown_use("as_is", 0.0, {}).wrong is False

    minor_ok = classify_shown_use("edited", 0.12, {"sentences_added": 0})
    assert minor_ok.sendable is True and minor_ok.wrong is False

    major_edit = classify_shown_use("edited", 0.45, {"sentences_added": 1})
    assert major_edit.sendable is False and major_edit.wrong is False

    amount_edit = classify_shown_use("edited", 0.05, {"amounts_added": [320000]})
    assert amount_edit.sendable is False and amount_edit.wrong is True

    discarded = classify_shown_use("discarded", 1.0, {})
    assert discarded.sendable is False and discarded.wrong is False

    conflict_as_is = classify_shown_use("as_is", 0.0, {}, amount_conflict=True)
    assert conflict_as_is.sendable is False and conflict_as_is.wrong is True


def test_shown_source_readiness_does_not_require_judge_calibration():
    st = IntentStats(
        intent="price_quote",
        total=40,
        unanswered=0,
        scored=40,
        sendable=38,
        wrong=0,
        amount_conflicts_7d=0,
        handoff_extra=0,
        handoff_missing=0,
        median_bot_lead_s=60.0,
    )
    assert readiness(st, judge_calibrated=False, source="blind").ready is False
    assert readiness(st, judge_calibrated=False, source="shown").ready is True


def test_intent_priority_score_ranks_wrong_higher_than_discard():
    high_wrong = intent_priority_score(0.3, 0.1)
    high_discard = intent_priority_score(0.0, 0.5)
    assert high_wrong > high_discard > intent_priority_score(0.0, 0.0)
