"""usage events: append-only LLM usage and cost per call (step 2.4)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-22
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UP = [
    """
    CREATE TABLE usage_events (
      id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      tenant_id         uuid NOT NULL REFERENCES tenants(id),
      run_id            uuid,                       -- agent_runs ra đời ở bước 2.5
      purpose           text NOT NULL,              -- tên route: classify | draft | eval
      provider          text NOT NULL,
      model             text NOT NULL,
      input_tokens      int NOT NULL CHECK (input_tokens >= 0),
      cache_read_tokens int NOT NULL CHECK (cache_read_tokens >= 0),
      cache_write_tokens int NOT NULL CHECK (cache_write_tokens >= 0),
      output_tokens     int NOT NULL CHECK (output_tokens >= 0),
      cost_micro        bigint NOT NULL CHECK (cost_micro >= 0),   -- micro-USD
      price_version     text NOT NULL,              -- để tính lại khi bảng giá đổi
      failed_over_from  text,                       -- vd 'anthropic:timeout' (đích đã lỗi trước đó)
      created_at        timestamptz NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX ix_usage_tenant_day ON usage_events (tenant_id, created_at)",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS usage_events")
