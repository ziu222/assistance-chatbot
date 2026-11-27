"""Kiểm thử hàm thuần phân loại HTTP status và backoff cho `outbound_deliveries`."""

from datetime import timedelta

from app.modules.outbound.domain.delivery import (
    MAX_DELIVERY_ATTEMPTS,
    DeliveryOutcome,
    classify_http_status,
    next_retry_delay,
)


def test_classify_http_status_outcomes():
    assert MAX_DELIVERY_ATTEMPTS == 5

    assert classify_http_status(200, "") == DeliveryOutcome(ok=True, retryable=False)
    assert classify_http_status(201, "") == DeliveryOutcome(ok=True, retryable=False)

    r400 = classify_http_status(400, '{"error":{"message":"Outside window"}}')
    assert r400.ok is False and r400.retryable is False
    assert r400.error and r400.error.startswith("http_400:")

    r401 = classify_http_status(401, "Invalid OAuth token")
    assert r401.ok is False and r401.retryable is False

    r429 = classify_http_status(429, "Rate limit exceeded")
    assert r429.ok is False and r429.retryable is True

    r500 = classify_http_status(500, "Internal error")
    assert r500.ok is False and r500.retryable is True

    r503 = classify_http_status(503, "Service unavailable")
    assert r503.ok is False and r503.retryable is True


def test_next_retry_delay_schedule():
    assert next_retry_delay(1) == timedelta(seconds=5)
    assert next_retry_delay(2) == timedelta(seconds=15)
    assert next_retry_delay(3) == timedelta(seconds=45)
    assert next_retry_delay(4) == timedelta(seconds=120)
    assert next_retry_delay(5) == timedelta(seconds=120)
