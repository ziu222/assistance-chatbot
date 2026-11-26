"""Kiểm thử hàm thuần adapter Facebook Messenger: HMAC-SHA256 và parse payload Meta."""

import hashlib
import hmac
from datetime import UTC, datetime

from app.modules.integration.domain.messenger import (
    MessengerInbound,
    parse_webhook_payload,
    verify_signature,
)


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_signature_cases():
    body = b'{"object":"page","entry":[]}'
    secret = "fb-app-secret-123"
    valid_sig = _sign(body, secret)

    assert verify_signature(body, valid_sig, secret) is True
    assert verify_signature(body, _sign(body, "wrong-secret"), secret) is False
    assert verify_signature(body, valid_sig.removeprefix("sha256="), secret) is False
    assert verify_signature(body, None, secret) is False
    assert verify_signature(body, "", secret) is False
    assert verify_signature(body, "sha256=", secret) is False
    assert verify_signature(body, valid_sig, "") is False


def test_parse_webhook_payload_filters_and_parses_meta_events():
    ts_ms_1 = 1_732_680_000_000
    ts_ms_2 = 1_732_680_005_000
    payload = {
        "object": "page",
        "entry": [
            {
                "id": "PAGE_1",
                "time": ts_ms_1,
                "messaging": [
                    {
                        "sender": {"id": "PSID_1"},
                        "recipient": {"id": "PAGE_1"},
                        "timestamp": ts_ms_1,
                        "message": {"mid": "m_1", "text": "Shop còn áo khoác không ạ?"},
                    },
                    {
                        "sender": {"id": "PAGE_1"},
                        "recipient": {"id": "PSID_1"},
                        "timestamp": ts_ms_1 + 1000,
                        "message": {
                            "mid": "m_echo",
                            "is_echo": True,
                            "text": "Tin echo của page",
                        },
                    },
                    {
                        "sender": {"id": "PSID_1"},
                        "recipient": {"id": "PAGE_1"},
                        "timestamp": ts_ms_1 + 2000,
                        "delivery": {"mids": ["m_echo"], "watermark": ts_ms_1 + 2000},
                    },
                    {
                        "sender": {"id": "PSID_1"},
                        "recipient": {"id": "PAGE_1"},
                        "timestamp": ts_ms_1 + 3000,
                        "read": {"watermark": ts_ms_1 + 3000},
                    },
                    {
                        "sender": {"id": "PSID_1"},
                        "recipient": {"id": "PAGE_1"},
                        "timestamp": ts_ms_1 + 3500,
                        "reaction": {"mid": "m_1", "action": "react", "emoji": "❤️"},
                    },
                    {
                        "sender": {"id": "PSID_1"},
                        "recipient": {"id": "PAGE_1"},
                        "timestamp": ts_ms_1 + 4000,
                        "message": {
                            "mid": "m_img",
                            "attachments": [{"type": "image", "payload": {"url": "https://x"}}],
                        },
                    },
                ],
            },
            {
                "id": "PAGE_2",
                "time": ts_ms_2,
                "messaging": [
                    {
                        "sender": {"id": "PSID_2"},
                        "recipient": {"id": "PAGE_2"},
                        "timestamp": ts_ms_2,
                        "message": {"mid": "m_2", "text": "   "},
                    },
                    {
                        "sender": {"id": "PSID_3"},
                        "recipient": {"id": "PAGE_2"},
                        "timestamp": ts_ms_2,
                        "message": {"mid": "m_3", "text": "Giá bao nhiêu shop?"},
                    },
                ],
            },
        ],
    }

    parsed = parse_webhook_payload(payload)
    assert parsed == [
        MessengerInbound(
            page_id="PAGE_1",
            sender_psid="PSID_1",
            message_id="m_1",
            text="Shop còn áo khoác không ạ?",
            occurred_at=datetime.fromtimestamp(ts_ms_1 / 1000.0, tz=UTC),
        ),
        MessengerInbound(
            page_id="PAGE_2",
            sender_psid="PSID_3",
            message_id="m_3",
            text="Giá bao nhiêu shop?",
            occurred_at=datetime.fromtimestamp(ts_ms_2 / 1000.0, tz=UTC),
        ),
    ]

    assert parse_webhook_payload({"object": "instagram", "entry": payload["entry"]}) == []
