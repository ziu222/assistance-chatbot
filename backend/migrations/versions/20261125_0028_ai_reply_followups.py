"""ai_reply_followups table for post-send monitoring and auto-demotion (design 06 A4, ADR-0039)

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-25
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

UP = [
    """
    CREATE TABLE ai_reply_followups (
      id         uuid PRIMARY KEY,
      tenant_id  uuid NOT NULL REFERENCES tenants(id),
      message_id uuid NOT NULL,
      agent_id   uuid NOT NULL,
      intent     text NOT NULL,
      kind       text NOT NULL CHECK (
        kind IN ('staff_correction','customer_reaction','owner_label','amount_conflict')
      ),
      detail     jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, message_id, kind),
      FOREIGN KEY (tenant_id, message_id) REFERENCES messages(tenant_id, id)
    )""",
    "CREATE INDEX ix_ai_reply_followups_intent "
    "ON ai_reply_followups (tenant_id, agent_id, intent, created_at DESC)",
]

DOWN = [
    "DROP TABLE IF EXISTS ai_reply_followups",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
