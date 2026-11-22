"""Giờ làm việc của shop: hàm thuần, không I/O (thiết kế bước 2 §12.4).

Mỗi khoảng `[start, end)` theo giờ địa phương; `end <= start` nghĩa là vắt qua nửa đêm
(`["20:00", "02:00"]` mở tới 2 giờ sáng hôm sau). Ngày ngoại lệ đóng cửa các khoảng **bắt đầu**
trong ngày đó.
"""

from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

Day = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAYS: tuple[Day, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# ponytail: tìm tối đa ~1 năm; shop đóng cửa lâu hơn thì next_open = None.
_HORIZON_DAYS = 370


class ClosedDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    note: str = Field(default="", max_length=100)


class BusinessHours(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = "Asia/Ho_Chi_Minh"
    weekly: dict[Day, list[tuple[time, time]]] = Field(default_factory=dict)  # thiếu ngày = nghỉ
    closed_days: list[ClosedDay] = Field(default_factory=list, max_length=100)
    pause_until: datetime | None = None  # "tạm nghỉ đến…", tự hết hạn

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown timezone") from exc
        return v

    @field_validator("weekly")
    @classmethod
    def _intervals(cls, v: dict[Day, list[tuple[time, time]]]) -> dict:
        for day, spans in v.items():
            if len(spans) > 4:
                raise ValueError(f"{day}: at most 4 intervals")
            if any(start == end for start, end in spans):
                raise ValueError(f"{day}: empty interval")
        return v

    @field_validator("pause_until")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("pause_until must include a timezone")
        return v

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _windows(hours: BusinessHours, start_day: date) -> Iterator[tuple[datetime, datetime]]:
    tz, closed = hours.zone, {c.date for c in hours.closed_days}
    for offset in range(_HORIZON_DAYS):
        d = start_day + timedelta(days=offset)
        if d in closed:
            continue
        for s, e in sorted(hours.weekly.get(DAYS[d.weekday()], [])):
            begin = datetime.combine(d, s, tzinfo=tz)
            end = datetime.combine(d + timedelta(days=1) if e <= s else d, e, tzinfo=tz)
            yield begin, end


def is_paused(hours: BusinessHours, now: datetime) -> bool:
    return hours.pause_until is not None and now < hours.pause_until


def is_open(hours: BusinessHours, now: datetime) -> bool:
    if is_paused(hours, now):
        return False
    yesterday = now.astimezone(hours.zone).date() - timedelta(days=1)  # khoảng vắt nửa đêm
    for begin, end in _windows(hours, yesterday):
        if begin > now:
            return False
        if now < end:
            return True
    return False


def next_open(hours: BusinessHours, now: datetime) -> datetime | None:
    """Lần mở cửa kế tiếp sau `now` (và sau khi hết tạm nghỉ). None nếu không có trong ~1 năm."""
    after = max(now, hours.pause_until) if is_paused(hours, now) else now
    yesterday = after.astimezone(hours.zone).date() - timedelta(days=1)
    for begin, end in _windows(hours, yesterday):
        if begin > after:
            return begin
        if begin <= after < end and after > now:  # tạm nghỉ hết giữa giờ mở cửa
            return after
    return None


_VI_DAYS = ("thứ Hai", "thứ Ba", "thứ Tư", "thứ Năm", "thứ Sáu", "thứ Bảy", "Chủ nhật")


def format_next_open(hours: BusinessHours, at: datetime | None, now: datetime) -> str:
    """'08:00 ngày mai', '13:30 hôm nay', '08:00 thứ Hai (24/11)'."""
    if at is None:
        return "khi shop mở cửa lại"
    local, today = at.astimezone(hours.zone), now.astimezone(hours.zone).date()
    clock = local.strftime("%H:%M")
    if local.date() == today:
        return f"{clock} hôm nay"
    if local.date() == today + timedelta(days=1):
        return f"{clock} ngày mai"
    return f"{clock} {_VI_DAYS[local.weekday()]} ({local:%d/%m})"
