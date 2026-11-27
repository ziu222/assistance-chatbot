"""Quy tắc adapter kênh (Messenger 24h, Zalo OA), `ChannelCapabilities` và độ mới catalog
(thiết kế 03 §6, ADR-0036, thiết kế 06 §5.2, §5.4, A6).

Hàm thuần, không I/O:
- `ChannelCapabilities`: khai báo cửa sổ trả lời, hỗ trợ link/nút bấm, độ dài tối đa của từng kênh.
- Messenger: chỉ cho phép tự gửi trong cửa sổ 24 giờ kể từ tin khách cuối.
- Zalo OA: theo quy định tin phản hồi của Zalo OA (trong 48 giờ kể từ tin khách cuối, tối đa 8 tin
  phản hồi liên tiếp, và chưa hết hạn mức cấu hình trên kết nối).
- Kênh khác (Shopee, TikTok Shop, ...): mặc định chặn (`unsupported_provider`).
- `is_catalog_stale`: khi nháp có số tiền trích từ sản phẩm catalog đồng bộ cũ hơn ngưỡng (hoặc
  sản phẩm đã bị ẩn/xóa), chặn bằng `catalog_stale` để lùi về Learning.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.shared.domain.text import extract_amounts

MESSENGER_WINDOW = timedelta(hours=24)
ZALO_OA_WINDOW = timedelta(hours=48)
ZALO_OA_MAX_REPLIES = 8
DEFAULT_CATALOG_MAX_AGE_HOURS = 24


@dataclass(frozen=True)
class ChannelCapabilities:
    reply_window: timedelta | None
    supports_links: bool
    supports_buttons: bool
    max_text_length: int


CHANNEL_CAPABILITIES: dict[str, ChannelCapabilities] = {
    "webchat": ChannelCapabilities(
        reply_window=None,
        supports_links=True,
        supports_buttons=False,
        max_text_length=16_000,
    ),
    "messenger": ChannelCapabilities(
        reply_window=MESSENGER_WINDOW,
        supports_links=True,
        supports_buttons=True,
        max_text_length=2_000,
    ),
    "zalo_oa": ChannelCapabilities(
        reply_window=ZALO_OA_WINDOW,
        supports_links=True,
        supports_buttons=True,
        max_text_length=2_000,
    ),
}


def capabilities_for(provider: str) -> ChannelCapabilities | None:
    """Trả `ChannelCapabilities` của kênh, hoặc `None` nếu kênh chưa được hỗ trợ."""
    return CHANNEL_CAPABILITIES.get(provider)


@dataclass(frozen=True)
class ChannelDecision:
    allowed: bool
    reason: str | None = None


def check_channel_send(
    provider: str,
    *,
    last_customer_at: datetime | None,
    now: datetime,
    replies_since_customer: int = 0,
    config: Mapping[str, Any] | None = None,
    text_length: int | None = None,
) -> ChannelDecision:
    """Kiểm tra điều kiện gửi tin theo từng adapter kênh (cửa sổ, hạn mức, độ dài tối đa)."""
    capabilities = capabilities_for(provider)
    if capabilities is None:
        return ChannelDecision(False, "unsupported_provider")

    if provider == "messenger":
        if last_customer_at is None or (now - last_customer_at) > MESSENGER_WINDOW:
            return ChannelDecision(False, "messenger_window_expired")
    elif provider == "zalo_oa":
        cfg = config or {}
        if cfg.get("quota_exhausted"):
            return ChannelDecision(False, "zalo_quota_exhausted")
        if last_customer_at is None or (now - last_customer_at) > ZALO_OA_WINDOW:
            return ChannelDecision(False, "zalo_window_expired")
        if replies_since_customer >= ZALO_OA_MAX_REPLIES:
            return ChannelDecision(False, "zalo_reply_limit")

    if text_length is not None and text_length > capabilities.max_text_length:
        return ChannelDecision(False, "message_too_long")
    return ChannelDecision(True)


def is_catalog_stale(
    *,
    reply_text: str,
    cited_product_synced_at: Sequence[datetime | None],
    missing_products: bool = False,
    now: datetime,
    max_age_hours: int = DEFAULT_CATALOG_MAX_AGE_HOURS,
) -> bool:
    """Trả True nếu nháp chứa số tiền từ sản phẩm catalog đồng bộ cũ hơn `max_age_hours`."""
    if not extract_amounts(reply_text):
        return False
    if missing_products:
        return True
    if not cited_product_synced_at:
        return False
    cutoff = timedelta(hours=max_age_hours)
    return any(ts is None or (now - ts) > cutoff for ts in cited_product_synced_at)
