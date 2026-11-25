"""Cổng gửi thuần mặc định chặn cho Autopilot (thiết kế 06 §5.2, ADR-0039).

Hàm thuần, không I/O. Thiếu bất kỳ điều kiện nào ⇒ chặn kèm lý do đọc được trong trace.
Không có cờ nào bỏ qua cổng.
"""

from dataclasses import dataclass, field

DEFAULT_MAX_BOT_STREAK = 3
# Kênh bot tự gửi được: phải có adapter thật sự giao tin tới khách. Messenger/Zalo OA mới có quy
# tắc cửa sổ (`domain/channels.py`), chưa có adapter gửi, nên tin ghi vào DB sẽ không tới khách.
# ponytail: thêm "messenger"/"zalo_oa" vào đây cùng lúc với adapter gửi của kênh đó.
AI_SEND_PROVIDERS = frozenset({"webchat"})
# Các ý định không bao giờ được bật Autopilot (thiết kế 06 §2, §4.1).
NEVER_AUTOPILOT_INTENTS = frozenset(
    {"other", "order_intent", "complaint", "human_request", "discount_request"}
)


@dataclass(frozen=True)
class AiGateInput:
    """Đầu vào đã kiểm của cổng gửi; mặc định đóng (fail-closed) nếu thiếu dữ liệu."""

    ai_enabled: bool = False
    auto_send: bool = False
    intents: tuple[str, ...] = ()
    enabled_intents: frozenset[str] = frozenset()
    handoff_intents: frozenset[str] = frozenset()
    run_config_key: dict | None = None
    enabled_config_keys: dict[str, dict] = field(default_factory=dict)
    grounded: bool = False
    validation_problems: tuple[str, ...] = ()
    needs_human: bool = True
    order_active: bool = True  # lượt đang ở luồng đặt hàng (thiết kế 03)
    handling: str = ""
    conversation_status: str = ""
    based_on_sequence: int = 0
    latest_customer_sequence: int = 0
    human_replied_after: bool = False
    bot_streak: int = 0
    max_bot_streak: int = DEFAULT_MAX_BOT_STREAK
    provider: str = ""
    channel_allowed: bool = True
    catalog_stale: bool = False


def send_decision(g: AiGateInput) -> str | None:
    """Trả `None` nếu được phép gửi, ngược lại trả lý do đầu tiên bị chặn (§5.2)."""
    if not g.ai_enabled or not g.auto_send:
        return "kill_switch"
    blocked_intents = NEVER_AUTOPILOT_INTENTS | g.handoff_intents
    if not g.intents or any(i in blocked_intents or i not in g.enabled_intents for i in g.intents):
        return "intent_not_enabled"
    if g.run_config_key is None or any(
        g.enabled_config_keys.get(i) != g.run_config_key for i in g.intents
    ):
        return "config_changed"
    if not g.grounded or g.validation_problems:
        return "not_grounded"
    if g.needs_human:
        return "needs_human"
    if g.order_active:  # thu thập đơn hàng là việc của nhân viên ở bước 5 (thiết kế 06 §2, O6)
        return "order_flow"
    if g.handling != "bot" or g.conversation_status != "open":
        return "handling"
    if (
        g.based_on_sequence <= 0
        or g.latest_customer_sequence > g.based_on_sequence
        or g.human_replied_after
    ):
        return "stale"
    if g.bot_streak >= g.max_bot_streak:
        return "bot_streak"
    if g.provider not in AI_SEND_PROVIDERS or not g.channel_allowed:
        return "channel"
    if g.catalog_stale:
        return "catalog_stale"
    return None
