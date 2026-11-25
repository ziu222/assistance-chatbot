"""Quy tắc adapter kênh (Messenger 24h, Zalo OA) và độ mới catalog (thiết kế 06 §5.2, §5.4, A6).

Hàm thuần, không I/O:
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
) -> ChannelDecision:
    """Kiểm tra điều kiện gửi tin trả lời tự động theo từng adapter kênh (§5.4)."""
    if provider == "webchat":
        return ChannelDecision(True)

    if provider == "messenger":
        if last_customer_at is None or (now - last_customer_at) > MESSENGER_WINDOW:
            return ChannelDecision(False, "messenger_window_expired")
        return ChannelDecision(True)

    if provider == "zalo_oa":
        cfg = config or {}
        if cfg.get("quota_exhausted"):
            return ChannelDecision(False, "zalo_quota_exhausted")
        if last_customer_at is None or (now - last_customer_at) > ZALO_OA_WINDOW:
            return ChannelDecision(False, "zalo_window_expired")
        if replies_since_customer >= ZALO_OA_MAX_REPLIES:
            return ChannelDecision(False, "zalo_reply_limit")
        return ChannelDecision(True)

    return ChannelDecision(False, "unsupported_provider")


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
