"""agent run requests: durable outbox consumer with debounce and leases (ADR-0008)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-21
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UP = [
    """
    CREATE TABLE conversation_run_requests (
      conversation_id uuid PRIMARY KEY,                 -- tối đa MỘT yêu cầu/hội thoại
      tenant_id       uuid NOT NULL,
      from_sequence   bigint NOT NULL,
      to_sequence     bigint NOT NULL CHECK (to_sequence >= from_sequence),
      first_seen_at   timestamptz NOT NULL,
      due_at          timestamptz NOT NULL,             -- debounce: chạy khi due_at <= now()
      lease_until     timestamptz,                      -- đang có worker xử lý
      lease_token     uuid,                             -- fencing: chỉ chủ lease hiện tại được ghi
      attempts        int NOT NULL DEFAULT 0,
      FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id)
    )""",
    "CREATE INDEX ix_run_requests_due ON conversation_run_requests (due_at)",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS conversation_run_requests")
