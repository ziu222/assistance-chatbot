"""Kiểm thử `ChannelCapabilities`, `capabilities_for` và giới hạn độ dài tin."""

from datetime import UTC, datetime, timedelta

from app.modules.outbound.domain.channels import (
    ChannelCapabilities,
    capabilities_for,
    check_channel_send,
)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def test_capabilities_for_known_and_unknown_providers():
    assert capabilities_for("webchat") == ChannelCapabilities(
        reply_window=None,
        supports_links=True,
        supports_buttons=False,
        max_text_length=4_000,
    )
    assert capabilities_for("messenger") == ChannelCapabilities(
        reply_window=timedelta(hours=24),
        supports_links=True,
        supports_buttons=True,
        max_text_length=2_000,
    )
    assert capabilities_for("zalo_oa") == ChannelCapabilities(
        reply_window=timedelta(hours=48),
        supports_links=True,
        supports_buttons=True,
        max_text_length=2_000,
    )
    assert capabilities_for("shopee") is None
    assert capabilities_for("unknown") is None


def test_check_channel_send_enforces_max_text_length():
    # Messenger: đúng 2000 ký tự ⇒ cho gửi; 2001 ⇒ message_too_long
    ok_msg = check_channel_send(
        "messenger",
        last_customer_at=NOW - timedelta(hours=1),
        now=NOW,
        text_length=2_000,
    )
    assert ok_msg.allowed is True

    too_long_msg = check_channel_send(
        "messenger",
        last_customer_at=NOW - timedelta(hours=1),
        now=NOW,
        text_length=2_001,
    )
    assert too_long_msg.allowed is False
    assert too_long_msg.reason == "message_too_long"

    # Webchat cho tới 4000 ký tự; vượt quá ⇒ message_too_long
    assert check_channel_send("webchat", last_customer_at=None, now=NOW, text_length=4_000).allowed
    too_long_web = check_channel_send("webchat", last_customer_at=None, now=NOW, text_length=4_001)
    assert too_long_web.allowed is False and too_long_web.reason == "message_too_long"

    # Zalo OA: quá 2000 ký tự khi vẫn trong cửa sổ ⇒ message_too_long
    too_long_zalo = check_channel_send(
        "zalo_oa",
        last_customer_at=NOW - timedelta(hours=1),
        now=NOW,
        text_length=2_001,
    )
    assert too_long_zalo.allowed is False and too_long_zalo.reason == "message_too_long"

    # Không truyền text_length (None) ⇒ giữ tương thích ngược mọi caller cũ
    assert check_channel_send("messenger", last_customer_at=NOW, now=NOW).allowed is True
