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

# Bước 2: hàng đợi agent (ADR-0008)
AGENT_RUN_REQUESTS = Counter(
    "agent_run_requests_total", "Agent run requests processed by outcome", ["outcome"]
)
AGENT_RUN_REQUEST_LAG = Histogram(
    "agent_run_request_lag_seconds",
    "Delay between due_at and claim",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)
DEBOUNCE_COALESCED = Histogram(
    "agent_debounce_coalesced_messages",
    "Customer messages handled by one run",
    buckets=(1, 2, 3, 5, 8, 13),
)

# Agent runtime (bước 2.5)
AGENT_RUNS = Counter("agent_runs_total", "Agent runs by status", ["status", "skip_reason"])
AGENT_RUN_DURATION = Histogram(
    "agent_run_duration_seconds",
    "Wall time of one agent run",
    buckets=(0.05, 0.25, 1, 2, 5, 10, 20, 40, 60),
)
AGENT_DRAFTS = Counter("agent_drafts_total", "Drafts saved", ["grounded", "needs_human"])
AGENT_NO_LLM = Counter("agent_no_llm_total", "Runs finished without calling an LLM")

# Lớp provider LLM (ADR-0015)
LLM_REQUESTS = Counter(
    "llm_requests_total", "LLM calls by outcome", ["provider", "model", "outcome"]
)
LLM_FAILOVER = Counter(
    "llm_failover_total",
    "Requests served by a later target after an earlier one failed",
    ["from_provider", "to_provider", "reason"],
)
LLM_CIRCUIT_OPEN = Gauge(
    "llm_circuit_open", "1 if the provider circuit is not closed", ["provider"]
)
LLM_LATENCY = Histogram(
    "llm_request_duration_seconds",
    "Latency of successful LLM calls",
    ["provider"],
    buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 30, 60),
)
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens by kind", ["provider", "kind"])
LLM_COST_MICRO = Counter("llm_cost_micro_total", "LLM cost in micro-USD", ["provider"])
SHADOW_COMPARISONS = Counter(
    "shadow_comparisons_total", "Shadow drafts compared with staff replies", ["status", "verdict"]
)
SHADOW_PAIRING_LAG = Histogram(
    "shadow_pairing_lag_seconds",
    "Time from the staff reply to its comparison",
    buckets=(60, 180, 600, 1800, 3600, 21600, 86400),
)
