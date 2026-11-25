"""Kiểm thử hàm thuần adapter kênh (Messenger 24h, Zalo OA) và độ mới catalog (06 A6)."""

from datetime import UTC, datetime, timedelta

from app.modules.outbound.domain.channels import (
    MESSENGER_WINDOW,
    ZALO_OA_MAX_REPLIES,
    ZALO_OA_WINDOW,
    check_channel_send,
    is_catalog_stale,
)

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def test_webchat_and_unsupported_providers():
    assert check_channel_send("webchat", last_customer_at=None, now=NOW).allowed is True
    for bad in ("shopee", "tiktok", "", "unknown"):
        res = check_channel_send(bad, last_customer_at=NOW, now=NOW)
        assert not res.allowed and res.reason == "unsupported_provider"


def test_messenger_24h_window():
    # Trong 24h (kể cả đúng ranh giới 24h) ⇒ cho gửi
    assert check_channel_send(
        "messenger", last_customer_at=NOW - timedelta(hours=1), now=NOW
    ).allowed
    assert check_channel_send("messenger", last_customer_at=NOW - MESSENGER_WINDOW, now=NOW).allowed

    # Quá 24h hoặc thiếu thời gian tin khách ⇒ chặn
    expired = check_channel_send(
        "messenger", last_customer_at=NOW - MESSENGER_WINDOW - timedelta(seconds=1), now=NOW
    )
    assert not expired.allowed and expired.reason == "messenger_window_expired"

    missing = check_channel_send("messenger", last_customer_at=None, now=NOW)
    assert not missing.allowed and missing.reason == "messenger_window_expired"


def test_zalo_oa_reply_window_limit_and_quota():
    assert check_channel_send(
        "zalo_oa",
        last_customer_at=NOW - timedelta(hours=10),
        now=NOW,
        replies_since_customer=ZALO_OA_MAX_REPLIES - 1,
    ).allowed

    # Quá 48h ⇒ chặn
    expired = check_channel_send(
        "zalo_oa",
        last_customer_at=NOW - ZALO_OA_WINDOW - timedelta(minutes=1),
        now=NOW,
        replies_since_customer=0,
    )
    assert not expired.allowed and expired.reason == "zalo_window_expired"

    # Đạt giới hạn 8 tin phản hồi liên tiếp ⇒ chặn
    limit = check_channel_send(
        "zalo_oa",
        last_customer_at=NOW - timedelta(minutes=5),
        now=NOW,
        replies_since_customer=ZALO_OA_MAX_REPLIES,
    )
    assert not limit.allowed and limit.reason == "zalo_reply_limit"

    # Hết hạn mức OA trên cấu hình kết nối ⇒ chặn
    quota = check_channel_send(
        "zalo_oa",
        last_customer_at=NOW - timedelta(minutes=5),
        now=NOW,
        replies_since_customer=0,
        config={"quota_exhausted": True},
    )
    assert not quota.allowed and quota.reason == "zalo_quota_exhausted"


def test_is_catalog_stale_rules():
    fresh = NOW - timedelta(hours=2)
    stale = NOW - timedelta(hours=25)

    # Không chứa số tiền ⇒ không bao giờ coi là stale
    assert not is_catalog_stale(
        reply_text="Áo khoác gió có đủ size S, M, L ạ.",
        cited_product_synced_at=[stale],
        now=NOW,
        max_age_hours=24,
    )

    # Có số tiền nhưng chỉ trích FAQ (không có sản phẩm catalog) ⇒ không stale
    assert not is_catalog_stale(
        reply_text="Phí ship 30.000đ toàn quốc ạ.",
        cited_product_synced_at=[],
        now=NOW,
        max_age_hours=24,
    )

    # Sản phẩm đồng bộ còn mới ⇒ đạt
    assert not is_catalog_stale(
        reply_text="Áo khoác gió giá 320.000đ ạ.",
        cited_product_synced_at=[fresh],
        now=NOW,
        max_age_hours=24,
    )

    # Sản phẩm đồng bộ cũ hơn ngưỡng ⇒ catalog_stale
    assert is_catalog_stale(
        reply_text="Áo khoác gió giá 320.000đ ạ.",
        cited_product_synced_at=[fresh, stale],
        now=NOW,
        max_age_hours=24,
    )

    # Sản phẩm thiếu synced_at hoặc đã bị ẩn/không còn trong catalog ⇒ catalog_stale
    assert is_catalog_stale(
        reply_text="Áo khoác gió giá 320.000đ ạ.",
        cited_product_synced_at=[None],
        now=NOW,
    )
    assert is_catalog_stale(
        reply_text="Áo khoác gió giá 320.000đ ạ.",
        cited_product_synced_at=[],
        missing_products=True,
        now=NOW,
    )
