"""Hàm thuần xử lý webhook Facebook Messenger: xác minh HMAC-SHA256 và chuẩn hóa sự kiện.

Không I/O, không import framework/DB. Không bao giờ log secret hay nội dung tin nhắn.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class MessengerInbound:
    page_id: str
    sender_psid: str
    message_id: str
    text: str
    occurred_at: datetime


def verify_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    """Xác minh chữ ký `X-Hub-Signature-256` (`sha256=<hex>`) bằng so sánh hằng thời gian."""
    if not signature_header or not app_secret:
        return False
    if not signature_header.startswith("sha256="):
        return False
    provided = signature_header.removeprefix("sha256=")
    if not provided:
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def _occurred_at(messaging: dict[str, Any], entry: dict[str, Any]) -> datetime:
    ts = messaging.get("timestamp")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return datetime.fromtimestamp(ts / 1000.0, tz=UTC)
    entry_ts = entry.get("time")
    if isinstance(entry_ts, (int, float)) and not isinstance(entry_ts, bool):
        return datetime.fromtimestamp(entry_ts / 1000.0, tz=UTC)
    return datetime.now(UTC)


def parse_webhook_payload(payload: dict[str, Any]) -> list[MessengerInbound]:
    """Trích các tin nhắn văn bản của khách từ payload webhook Meta (`object == 'page'`).

    Bỏ qua tin echo (`is_echo`), delivery/read/reaction, và tin không có văn bản.
    """
    if not isinstance(payload, dict) or payload.get("object") != "page":
        return []

    events: list[MessengerInbound] = []
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return events

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        page_id = str(entry.get("id") or "").strip()
        if not page_id:
            continue
        messaging_list = entry.get("messaging")
        if not isinstance(messaging_list, list):
            continue

        for messaging in messaging_list:
            if not isinstance(messaging, dict):
                continue
            message = messaging.get("message")
            if not isinstance(message, dict):
                continue
            if message.get("is_echo"):
                continue

            raw_text = message.get("text")
            if not isinstance(raw_text, str) or not raw_text.strip():
                continue

            sender = messaging.get("sender")
            sender_psid = str(sender.get("id") or "").strip() if isinstance(sender, dict) else ""
            message_id = str(message.get("mid") or "").strip()
            if not sender_psid or not message_id:
                continue

            events.append(
                MessengerInbound(
                    page_id=page_id,
                    sender_psid=sender_psid,
                    message_id=message_id,
                    text=raw_text,
                    occurred_at=_occurred_at(messaging, entry),
                )
            )
    return events
