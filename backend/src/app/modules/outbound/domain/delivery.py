"""Hàm thuần và hợp đồng giao tin ra kênh ngoài (O5.3, ADR-0036).

Không I/O, không import framework/HTTP/DB.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

MAX_DELIVERY_ATTEMPTS = 5
_BACKOFF_SECONDS = (5, 15, 45, 120)


@dataclass(frozen=True)
class DeliveryOutcome:
    ok: bool
    retryable: bool
    provider_message_id: str | None = None
    error: str | None = None


class ChannelSender(Protocol):
    async def send(self, *, recipient_id: str, text: str, access_token: str) -> DeliveryOutcome: ...


def classify_http_status(status_code: int, error_body: str = "") -> DeliveryOutcome:
    """Phân loại phản hồi HTTP từ kênh ngoài: 2xx thành công, 429/5xx thử lại, 4xx dừng ngay."""
    if 200 <= status_code < 300:
        return DeliveryOutcome(ok=True, retryable=False)

    snippet = error_body.strip()[:300]
    err = f"http_{status_code}: {snippet}" if snippet else f"http_{status_code}"
    retryable = status_code == 429 or status_code >= 500
    return DeliveryOutcome(ok=False, retryable=retryable, error=err[:500])


def next_retry_delay(attempt: int) -> timedelta:
    """Thời gian chờ trước lần thử tiếp theo: 5s, 15s, 45s, 120s."""
    idx = max(0, min(attempt - 1, len(_BACKOFF_SECONDS) - 1))
    return timedelta(seconds=_BACKOFF_SECONDS[idx])
