"""Bảng tình huống của tầng chính sách và giờ làm việc (thiết kế bước 2 §12.11)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.modules.agent_runtime.signals import THRESHOLDS
from app.modules.conversation_policy.domain.escalation import ticket_priority
from app.modules.conversation_policy.domain.hours import (
    BusinessHours,
    ClosedDay,
    format_next_open,
    is_open,
    next_open,
)
from app.modules.conversation_policy.domain.policy import (
    HandoffAckConfig,
    PolicyConfig,
    PolicyState,
    SituationConfig,
    WelcomeConfig,
    decide,
    render_handoff_ack,
)
from app.modules.outbound.canned import GateInput, send_block

VN = ZoneInfo("Asia/Ho_Chi_Minh")
DAY = [(time(8), time(12)), (time(13, 30), time(21))]
WEEK = {d: DAY for d in ("mon", "tue", "wed", "thu", "fri", "sat")}  # Chủ nhật nghỉ


def at(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=VN)


# 2026-11-21 là thứ Bảy, 2026-11-22 Chủ nhật, 2026-11-23 thứ Hai.
@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(2026, 11, 21, 7, 59), False),
        (at(2026, 11, 21, 8, 0), True),
        (at(2026, 11, 21, 12, 0), False),  # nghỉ trưa: end không tính
        (at(2026, 11, 21, 13, 30), True),
        (at(2026, 11, 21, 22, 10), False),
        (at(2026, 11, 22, 10, 0), False),  # Chủ nhật
        (datetime(2026, 11, 21, 3, 0, tzinfo=UTC), True),  # 10:00 giờ VN
    ],
)
def test_is_open_weekly(now, expected):
    assert is_open(BusinessHours(weekly=WEEK), now) is expected


def test_overnight_interval_spans_midnight():
    h = BusinessHours(weekly={"fri": [(time(20), time(2))]})
    assert is_open(h, at(2026, 11, 20, 23))  # thứ Sáu 23:00
    assert is_open(h, at(2026, 11, 21, 1, 59))  # rạng sáng thứ Bảy, vẫn khoảng của thứ Sáu
    assert not is_open(h, at(2026, 11, 21, 2))
    assert not is_open(h, at(2026, 11, 21, 23))  # thứ Bảy không có khoảng


def test_closed_day_and_next_open_skips_holidays():
    tet = BusinessHours(
        weekly={d: DAY for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")},
        closed_days=[ClosedDay(date=date(2027, 2, 6 + i), note="Tết") for i in range(4)],
    )
    assert not is_open(tet, at(2027, 2, 7, 10))
    assert next_open(tet, at(2027, 2, 5, 22)) == at(2027, 2, 10, 8)


def test_next_open_over_weekend_and_lunch():
    h = BusinessHours(weekly=WEEK)
    assert next_open(h, at(2026, 11, 21, 22, 10)) == at(2026, 11, 23, 8)  # T7 tối ⇒ T2
    assert next_open(h, at(2026, 11, 21, 12, 15)) == at(2026, 11, 21, 13, 30)
    assert next_open(BusinessHours(), at(2026, 11, 21, 9)) is None  # không có giờ nào


def test_pause_closes_and_expires():
    h = BusinessHours(weekly=WEEK, pause_until=at(2026, 11, 21, 15))
    assert not is_open(h, at(2026, 11, 21, 10))
    assert next_open(h, at(2026, 11, 21, 10)) == at(2026, 11, 21, 15)  # hết tạm nghỉ giữa giờ mở
    assert is_open(h, at(2026, 11, 21, 15))


def test_format_next_open():
    h = BusinessHours(weekly=WEEK)
    now = at(2026, 11, 21, 22)
    assert format_next_open(h, at(2026, 11, 22, 8), now) == "08:00 ngày mai"
    assert format_next_open(h, at(2026, 11, 21, 23), now) == "23:00 hôm nay"
    assert format_next_open(h, at(2026, 11, 23, 8), now) == "08:00 thứ Hai (23/11)"
    assert format_next_open(h, None, now) == "khi shop mở cửa lại"


def test_hours_validation():
    with pytest.raises(ValidationError):
        BusinessHours(timezone="Mars/Olympus")
    with pytest.raises(ValidationError):
        BusinessHours(weekly={"mon": [(time(8), time(8))]})
    with pytest.raises(ValidationError):
        BusinessHours(pause_until=datetime(2026, 11, 21, 15))  # thiếu múi giờ


# --- decide ---

CFG = PolicyConfig(
    hours=BusinessHours(weekly=WEEK),
    paused=SituationConfig(enabled=True, text="{shop_name} tạm nghỉ, mở lại {next_open} ạ."),
    after_hours=SituationConfig(
        enabled=True, text="Dạ {shop_name} chào bạn, shop mở cửa lúc {next_open} ạ."
    ),
    welcome=WelcomeConfig(enabled=True, text="Dạ {shop_name} chào bạn ạ!"),
)
OPEN, CLOSED = at(2026, 11, 21, 10), at(2026, 11, 21, 22, 10)


def state(handling="bot", new=False, prev=None, last=None) -> PolicyState:
    return PolicyState(handling, new, prev, last or {})


def run(cfg=CFG, st=None, now=OPEN):
    return decide(cfg, st or state(), now, shop_name="Áo Xinh")


@pytest.mark.parametrize(
    ("st", "now", "situation", "run_agent"),
    [
        (state(new=True), OPEN, "welcome", True),
        (state(new=False, prev=OPEN - timedelta(minutes=5)), OPEN, None, True),
        (state(prev=OPEN - timedelta(hours=25)), OPEN, "welcome", True),  # khách quay lại
        (state(new=True), CLOSED, "after_hours", True),  # một tin, không thêm câu chào
        (state(handling="human", new=True), OPEN, None, False),
        (state(handling="waiting_human", new=True), CLOSED, None, False),
        # ngoài giờ đã báo lúc 21:30 ⇒ 22:10 không báo lại; sáng mai 8:00 trở đi mới báo lại
        (state(last={"after_hours": at(2026, 11, 21, 21, 30)}), CLOSED, None, True),
        (state(last={"welcome": OPEN - timedelta(hours=1)}, new=True), OPEN, None, True),
    ],
)
def test_decide_table(st, now, situation, run_agent):
    d = run(st=st, now=now)
    assert (d.situation, d.run_agent) == (situation, run_agent)
    assert (d.text is None) == (situation is None)


def test_after_hours_text_is_rendered():
    d = run(st=state(new=True), now=CLOSED)
    assert d.text == "Dạ Áo Xinh chào bạn, shop mở cửa lúc 08:00 thứ Hai (23/11) ạ."


def test_after_hours_resent_in_next_closed_period():
    last = {"after_hours": at(2026, 11, 20, 22)}  # tối thứ Sáu; đã mở lại sáng thứ Bảy
    assert run(st=state(last=last), now=CLOSED).situation == "after_hours"


def test_pause_wins_over_after_hours():
    cfg = CFG.model_copy(
        update={"hours": CFG.hours.model_copy(update={"pause_until": at(2026, 11, 25, 8)})}
    )
    d = run(cfg=cfg, st=state(new=True), now=CLOSED)
    assert d.situation == "paused"
    assert d.text == "Áo Xinh tạm nghỉ, mở lại 08:00 thứ Tư (25/11) ạ."


def test_next_waiting_human_stops_agent():
    cfg = CFG.model_copy(
        update={"after_hours": CFG.after_hours.model_copy(update={"next": "waiting_human"})}
    )
    d = run(cfg=cfg, st=state(new=True), now=CLOSED)
    assert (d.situation, d.set_handling, d.run_agent) == ("after_hours", "waiting_human", False)


def test_disabled_situation_does_not_fall_through():
    cfg = CFG.model_copy(update={"after_hours": SituationConfig(enabled=False)})
    d = run(cfg=cfg, st=state(new=True), now=CLOSED)
    assert (d.situation, d.run_agent) == (None, True)


def test_no_hours_means_always_open():
    d = run(cfg=PolicyConfig(welcome=CFG.welcome), st=state(new=True), now=CLOSED)
    assert d.situation == "welcome"


def test_situation_validation():
    with pytest.raises(ValidationError):
        SituationConfig(enabled=True, text="  ")
    with pytest.raises(ValidationError):
        SituationConfig(enabled=True, text="Chào {customer_name}")
    assert SituationConfig(enabled=False, text="").text == ""


def test_handoff_ack_config_and_render():
    with pytest.raises(ValidationError):
        HandoffAckConfig(enabled=True, text="   ")
    with pytest.raises(ValidationError):
        HandoffAckConfig(enabled=True, text="Xin chào {unknown}")

    cfg = PolicyConfig(
        hours=BusinessHours(weekly=WEEK),
        handoff_ack=HandoffAckConfig(
            enabled=True, text="Dạ {shop_name} đã chuyển nhân viên, mở lại {next_open} ạ."
        ),
    )
    assert render_handoff_ack(PolicyConfig(), OPEN, shop_name="Áo Xinh") is None
    assert (
        render_handoff_ack(cfg, CLOSED, shop_name="Áo Xinh")
        == "Dạ Áo Xinh đã chuyển nhân viên, mở lại 08:00 thứ Hai (23/11) ạ."
    )


def test_ticket_priority_levels():
    assert ticket_priority("topic", 0, "low") == 0
    assert ticket_priority("order", 1, "low") == 0
    assert ticket_priority("customer_button", 0, "low") == 1
    assert ticket_priority("phrase", 0, "low") == 1
    assert ticket_priority("topic", 2, "low") == 1
    assert ticket_priority("topic", THRESHOLDS["low"], "low") == 2
    assert ticket_priority("topic", THRESHOLDS["medium"], "medium") == 2
    assert ticket_priority("topic", THRESHOLDS["high"], "high") == 2


def test_send_block_handoff_ack_fail_closed():
    assert send_block(GateInput(True, "webchat", "open", "waiting_human")) == "human_handling"
    assert (
        send_block(GateInput(True, "webchat", "open", "waiting_human", allow_waiting_human=True))
        is None
    )
    assert (
        send_block(GateInput(True, "webchat", "open", "human", allow_waiting_human=True))
        == "human_handling"
    )
    assert (
        send_block(GateInput(False, "webchat", "open", "waiting_human", allow_waiting_human=True))
        == "auto_send_off"
    )
