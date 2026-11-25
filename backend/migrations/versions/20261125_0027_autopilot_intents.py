"""autopilot_intents state table and tenant_policy.max_bot_streak (design 06 A2, ADR-0039)

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-25
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

UP = [
    """
    CREATE TABLE autopilot_intents (
      tenant_id     uuid NOT NULL REFERENCES tenants(id),
      agent_id      uuid NOT NULL,
      intent        text NOT NULL,
      state         text NOT NULL CHECK (state IN ('autopilot','learning')),
      config_key    jsonb NOT NULL,
      source        text NOT NULL CHECK (source IN ('blind','shown')),
      enabled_by    uuid,
      enabled_at    timestamptz,
      demoted_at    timestamptz,
      demote_reason text,
      updated_at    timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (tenant_id, agent_id, intent),
      FOREIGN KEY (tenant_id, agent_id) REFERENCES agents(tenant_id, id)
    )""",
    "CREATE INDEX ix_autopilot_intents_state ON autopilot_intents (tenant_id, state)",
    "ALTER TABLE tenant_policy ADD COLUMN max_bot_streak int NOT NULL DEFAULT 3 "
    "CHECK (max_bot_streak BETWEEN 1 AND 10)",
]

DOWN = [
    "ALTER TABLE tenant_policy DROP COLUMN IF EXISTS max_bot_streak",
    "DROP TABLE IF EXISTS autopilot_intents",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
