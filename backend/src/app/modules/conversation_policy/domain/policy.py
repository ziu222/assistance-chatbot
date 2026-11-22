"""Tầng chính sách hội thoại: quyết định ai được nói và nói gì, trước agent (bước 2 §12.2).

`decide` là hàm thuần: cùng cấu hình + trạng thái + thời điểm ⇒ cùng quyết định. Worker và trình
mô phỏng của chủ shop gọi đúng hàm này. Chủ shop không thấy "rule": họ chỉ bật/tắt một bộ tình
huống cố định, viết nội dung và chọn bot có tư vấn tiếp hay không. Ưu tiên nằm trong code.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.agent_runtime.rules import HANDOFF_PHRASES
from app.modules.conversation_policy.domain.hours import (
    BusinessHours,
    format_next_open,
    is_open,
    is_paused,
    next_open,
)
from app.modules.knowledge.domain.text import fold

Handling = Literal["bot", "waiting_human", "human"]
Situation = Literal["paused", "after_hours", "welcome"]
VARIABLES = frozenset({"shop_name", "next_open"})
_VAR = re.compile(r"\{([^{}]*)\}")


class SituationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    text: str = Field(default="", max_length=500)
    next: Literal["bot", "waiting_human"] = "bot"  # sau tin này: bot tư vấn hay chờ người

    @model_validator(mode="after")
    def _check(self) -> "SituationConfig":
        if self.enabled and not self.text.strip():
            raise ValueError("enabled situation needs a text")
        unknown = set(_VAR.findall(self.text)) - VARIABLES
        if unknown:
            raise ValueError(f"unknown variables: {', '.join(sorted(unknown))}")
        return self


class WelcomeConfig(SituationConfig):
    gap_hours: int = Field(default=24, ge=1, le=720)  # khách quay lại sau bao lâu thì chào lại


class PolicyConfig(BaseModel):
    """Nội dung của một `policy_release` (bất biến sau khi lưu)."""

    model_config = ConfigDict(extra="forbid")
    hours: BusinessHours | None = None  # None = chưa khai báo giờ ⇒ coi như luôn mở
    paused: SituationConfig = Field(default_factory=SituationConfig)
    after_hours: SituationConfig = Field(default_factory=SituationConfig)
    welcome: WelcomeConfig = Field(default_factory=WelcomeConfig)
    # Chủ đề shop đánh dấu "luôn cần nhân viên" (vd "đổi size", "bảo hành"): khớp ⇒ mở ticket.
    handoff_topics: list[Annotated[str, Field(min_length=2, max_length=50)]] = Field(
        default_factory=list, max_length=30
    )


@dataclass(frozen=True)
class PolicyState:
    handling: Handling
    is_new_conversation: bool  # cụm tin này chứa tin đầu tiên của hội thoại
    prev_message_at: datetime | None  # tin gần nhất TRƯỚC cụm này
    last_sent: Mapping[Situation, datetime]  # lần gần nhất mỗi tình huống được chọn


@dataclass(frozen=True)
class Decision:
    situation: Situation | None
    text: str | None  # đã thay biến; None nếu không có tin tự động
    set_handling: Handling | None  # None = giữ nguyên
    run_agent: bool


def _applies(cfg: PolicyConfig, state: PolicyState, now: datetime) -> Situation | None:
    hours, gap = cfg.hours, timedelta(hours=cfg.welcome.gap_hours)
    last = state.last_sent
    if hours is not None and is_paused(hours, now):
        # ponytail: "một lần mỗi lần tạm nghỉ" xấp xỉ bằng khoảng chào lại; lưu thời điểm bật
        # tạm nghỉ nếu shop tạm nghỉ hai lần sát nhau.
        sent = last.get("paused")
        return "paused" if sent is None or now - sent >= gap else None
    if hours is not None and not is_open(hours, now):
        sent = last.get("after_hours")
        if sent is not None:  # đã báo trong lần đóng cửa này (tới khi mở lại)
            reopen = next_open(hours, sent)
            if reopen is None or now < reopen:
                return None
        return "after_hours"
    returning = state.prev_message_at is not None and now - state.prev_message_at >= gap
    if state.is_new_conversation or returning:
        sent = last.get("welcome")
        return "welcome" if sent is None or now - sent >= gap else None
    return None


def render(text: str, *, shop_name: str, hours: BusinessHours | None, now: datetime) -> str:
    nxt = format_next_open(hours, next_open(hours, now), now) if hours else "khi shop mở cửa lại"
    return text.replace("{shop_name}", shop_name).replace("{next_open}", nxt)


def decide(cfg: PolicyConfig, state: PolicyState, now: datetime, *, shop_name: str) -> Decision:
    """Tối đa MỘT tin tự động mỗi lượt: tình huống ưu tiên cao nhất đang áp dụng thắng."""
    if state.handling != "bot":
        return Decision(None, None, None, False)
    situation = _applies(cfg, state, now)
    if situation is None:
        return Decision(None, None, None, True)
    conf: SituationConfig = getattr(cfg, situation)
    if not conf.enabled:
        # Tình huống áp dụng nhưng shop tắt: không chào, bot tư vấn như thường. Không rơi xuống
        # tình huống thấp hơn (ngoài giờ mà tắt câu ngoài giờ thì cũng không gửi câu chào).
        return Decision(None, None, None, True)
    text = render(conf.text, shop_name=shop_name, hours=cfg.hours, now=now)
    if conf.next == "waiting_human":
        return Decision(situation, text, "waiting_human", False)
    return Decision(situation, text, None, True)


def suggested_config() -> PolicyConfig:
    """Bản điền sẵn cho shop chưa cấu hình (onboarding). Chưa có hiệu lực tới khi chủ shop lưu."""
    day = [(time(8), time(21))]
    return PolicyConfig(
        hours=BusinessHours(weekly={d: day for d in ("mon", "tue", "wed", "thu", "fri", "sat")}),
        paused=SituationConfig(
            enabled=True,
            text="Dạ {shop_name} đang tạm nghỉ, shop sẽ trả lời bạn từ {next_open} ạ.",
        ),
        after_hours=SituationConfig(
            enabled=True,
            text="Dạ {shop_name} chào bạn! Shop đã hết giờ làm việc, mở lại lúc {next_open}. "
            "Em vẫn có thể giải đáp nhanh vài câu hỏi cho bạn ạ.",
        ),
        welcome=WelcomeConfig(
            enabled=True, text="Dạ {shop_name} chào bạn ạ! Bạn cần shop hỗ trợ gì ạ?"
        ),
    )


HandoffSource = Literal["phrase", "topic"]


def detect_handoff(
    cfg: PolicyConfig, customer_texts: list[str]
) -> tuple[HandoffSource, str] | None:
    """Tín hiệu XÁC ĐỊNH đòi gặp người (§12.7): cụm từ cố định, rồi chủ đề shop đánh dấu.
    Trả (nguồn, cụm đã khớp). Tín hiệu hành vi/ngữ nghĩa không nằm ở đây (2.10, chỉ ghi)."""
    text = " ".join(fold(t) for t in customer_texts)
    for phrase in HANDOFF_PHRASES:
        if phrase in text:
            return "phrase", phrase
    for topic in cfg.handoff_topics:
        if fold(topic) in text:
            return "topic", topic
    return None
