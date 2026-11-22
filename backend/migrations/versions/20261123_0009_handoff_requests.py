"""handoff tickets and learning signals (step 2.8)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-23
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

UP = [
    """
    CREATE TABLE handoff_requests (
      id              uuid PRIMARY KEY,
      tenant_id       uuid NOT NULL,
      conversation_id uuid NOT NULL,
      status          text NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open','claimed','resolved','dismissed')),
      source          text NOT NULL CHECK (source IN ('customer_button','phrase','topic','staff')),
      reason          text,                -- cụm từ / chủ đề đã khớp; không chép nội dung khách
      priority        smallint NOT NULL DEFAULT 0,
      claimed_by      uuid,
      opened_at       timestamptz NOT NULL DEFAULT now(),
      claimed_at      timestamptz,
      closed_at       timestamptz,
      not_needed      boolean,             -- nhãn của nhân viên: "ticket này không cần"
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id)
    )""",
    # Một ticket đang mở mỗi hội thoại: khách bấm nút 5 lần vẫn ra một ticket.
    """
    CREATE UNIQUE INDEX uq_handoff_one_active ON handoff_requests (tenant_id, conversation_id)
      WHERE status IN ('open','claimed')""",
    """
    CREATE INDEX ix_handoff_queue ON handoff_requests (tenant_id, priority DESC, opened_at)
      WHERE status IN ('open','claimed')""",
    """
    CREATE TABLE learning_signals (          -- append-only; bước 4 mới dùng
      id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      tenant_id       uuid NOT NULL,
      conversation_id uuid,
      kind            text NOT NULL,
      payload         jsonb NOT NULL DEFAULT '{}',   -- đã chiếu: id, nhãn; không nội dung khách
      created_at      timestamptz NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX ix_learning_signals_tenant ON learning_signals (tenant_id, created_at)",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for t in ("learning_signals", "handoff_requests"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
