"""Ghép nháp shadow với câu nhân viên gửi sau nó (thiết kế 04 §4, ADR-0037): hàm thuần.

Câu tham chiếu = các tin `human` liên tiếp sau `based_on_sequence`, tới tin khách kế tiếp hoặc tối
đa `REFERENCE_WINDOW` sau tin nhân viên đầu tiên. Tin `system` (tin tự động) không phải câu nhân
viên. Khách nhắn thêm trước khi nhân viên trả lời ⇒ nháp này bị thay bởi nháp mới hơn.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

REFERENCE_WINDOW = timedelta(minutes=10)  # nhân viên hay gửi nhiều tin ngắn liền nhau
SETTLE = timedelta(minutes=2)  # chờ nhân viên gõ xong rồi mới chấm
UNANSWERED_AFTER = timedelta(hours=24)

Status = Literal["paired", "superseded", "unanswered", "pending"]


@dataclass(frozen=True)
class Msg:
    sequence: int
    sender: str  # customer | human | system
    content: str
    at: datetime


@dataclass(frozen=True)
class Pair:
    status: Status
    reference: tuple[Msg, ...] = ()

    @property
    def text(self) -> str:
        return "\n".join(m.content for m in self.reference)

    @property
    def first_reply_at(self) -> datetime | None:
        return self.reference[0].at if self.reference else None


def pair(based_on: int, drafted_at: datetime, after: list[Msg], now: datetime) -> Pair:
    """`after`: mọi tin có sequence > `based_on`, theo thứ tự. `pending` = chưa đủ để kết luận."""
    talk = [m for m in sorted(after, key=lambda m: m.sequence) if m.sender != "system"]
    first = next((i for i, m in enumerate(talk) if m.sender == "human"), None)
    if first is None:
        if any(m.sender == "customer" for m in talk):
            return Pair("superseded")  # khách nhắn tiếp ⇒ đã có nháp mới hơn cho cả cụm
        return Pair("unanswered") if now - drafted_at > UNANSWERED_AFTER else Pair("pending")
    if any(m.sender == "customer" for m in talk[:first]):
        return Pair("superseded")
    start = talk[first].at
    ref: list[Msg] = []
    closed = False
    for m in talk[first:]:
        if m.sender == "customer" or m.at - start > REFERENCE_WINDOW:
            closed = True
            break
        ref.append(m)
    if not closed and now - ref[-1].at < SETTLE and now - start < REFERENCE_WINDOW:
        return Pair("pending")  # nhân viên có thể còn đang gõ tiếp
    return Pair("paired", tuple(ref))
