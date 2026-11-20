"""Metric Prometheus (thiết kế §13.2). Nhãn không chứa tenant/visitor/key để tránh bùng nhãn và
lộ định danh."""

from prometheus_client import Counter, Gauge, Histogram

INBOUND_MESSAGES = Counter(
    "inbound_messages_total", "Inbound messages by outcome", ["result", "reason"]
)
RATE_LIMITED = Counter("rate_limited_total", "Requests rejected by rate limit", ["scope"])
DB_TX_RETRIES = Counter("db_tx_retries_total", "Transactions retried after DB conflict")
HTTP_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP latency by route template", ["route", "method"]
)
SSE_CONNECTIONS = Gauge("sse_connections", "Open SSE streams", ["audience"])
SSE_RESYNC = Counter("sse_resync_total", "Resync events forced on clients")
OUTBOX_UNPUBLISHED = Gauge("outbox_unpublished_count", "Outbox events not yet published")
OUTBOX_OLDEST_AGE = Gauge("outbox_oldest_age_seconds", "Age of the oldest unpublished event")
OUTBOX_PUBLISH_LAG = Histogram(
    "outbox_publish_lag_seconds",
    "published_at - created_at",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
