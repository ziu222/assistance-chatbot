"""Tín hiệu leo thang chỉ ghi (bước 2.10): hành vi, ngữ nghĩa, điểm."""

from app.modules.agent_runtime.signals import (
    THRESHOLDS,
    behavior_signals,
    score,
    semantic_signals,
)


def test_calm_conversation_has_no_signal():
    assert behavior_signals(["áo khoác còn size M không"], ["giá bao nhiêu vậy shop"], 1) == []


def test_repeat_is_detected_without_accents():
    earlier = ["áo khoác gió size M còn hàng không shop"]
    assert "repeat" in behavior_signals(earlier, ["ao khoac gio size M con hang ko shop"], 1)
    assert "repeat" not in behavior_signals(earlier, ["ship về Đà Nẵng mất mấy ngày"], 1)
    assert "repeat" not in behavior_signals(["ok"], ["ok"], 1)  # câu quá ngắn: không tính


def test_negation_burst_long_and_shouting():
    found = behavior_signals(
        [], ["không phải", "em hỏi rồi mà", "trả lời đi", "sao lâu vậy??"], shop_turns=3
    )
    assert found == ["negation", "burst", "long_unresolved", "shouting"]


def test_semantic_needs_enough_turns():
    assert semantic_signals("high", True, turns=3) == []
    assert semantic_signals("high", True, turns=4) == ["frustration_high", "human_decision"]
    assert semantic_signals("none", False, turns=10) == []


def test_score_and_thresholds():
    assert score(["repeat", "negation"]) == 3
    assert score(["frustration_high", "human_decision"]) == 5
    assert THRESHOLDS["low"] > THRESHOLDS["medium"] > THRESHOLDS["high"]


def test_confusion_and_report():
    from app.modules.conversation_policy.domain.escalation import confusion, report

    rows = [(7, True), (5, False), (2, True), (0, False), (6, True)]
    c = confusion(rows, 5)
    assert (c.tp, c.fp, c.fn, c.tn) == (2, 1, 1, 1)
    assert c.precision == 2 / 3 and c.recall == 2 / 3
    empty = confusion([(0, False)], 5)
    assert empty.precision is None and empty.recall is None  # chưa có dự đoán/nhãn dương
    r = report(rows)
    assert r["conversations"] == 5 and r["needed_human"] == 3
    assert r["by_sensitivity"]["high"]["threshold"] == 3
