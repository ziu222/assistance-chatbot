"""Mức sẵn sàng tự trả lời theo ý định (thiết kế 04 §7)."""

from app.modules.shadow.domain.readiness import IntentStats, readiness


def stats(**over) -> IntentStats:
    base = dict(
        intent="price_quote",
        total=40,
        unanswered=2,
        scored=30,
        sendable=27,
        wrong=0,
        amount_conflicts_7d=0,
        handoff_extra=1,
        handoff_missing=0,
        median_bot_lead_s=95.0,
    )
    return IntentStats(**(base | over))


def test_ready_at_the_thresholds():
    assert readiness(stats(), judge_calibrated=True).ready  # 27/30 = 90% gửi được, 0% sai
    edge = stats(scored=50, sendable=43, wrong=1)  # 86% và 2%: vừa đủ
    assert readiness(edge, judge_calibrated=True).ready


def test_each_rule_explains_itself():
    r = readiness(stats(scored=12, sendable=12), judge_calibrated=True)
    assert r.reasons == ["chưa đủ mẫu (12/30 cặp được chấm)"]
    r = readiness(stats(wrong=2, sendable=25), judge_calibrated=False)
    assert not r.ready and len(r.reasons) == 3  # sai 7%, gửi được 83%, chưa hiệu chỉnh
    r = readiness(stats(amount_conflicts_7d=1), judge_calibrated=True)
    assert r.reasons == ["1 lần báo giá mâu thuẫn trong 7 ngày"]
