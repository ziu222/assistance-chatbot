"""regression eval cases and candidate eval report (design 05 L4, ADR-0038)

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-25
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE knowledge_candidates ADD COLUMN eval_report jsonb",
    """
    CREATE TABLE eval_cases (
      id           uuid PRIMARY KEY,
      tenant_id    uuid NOT NULL REFERENCES tenants(id),
      source       text NOT NULL CHECK (source IN ('lesson','manual')),
      candidate_id uuid,
      question     text NOT NULL,
      expected     jsonb NOT NULL DEFAULT '{}',
      active       boolean NOT NULL DEFAULT true,
      created_at   timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, candidate_id) REFERENCES knowledge_candidates(tenant_id, id)
    )""",
    "CREATE INDEX ix_eval_cases_tenant_active ON eval_cases (tenant_id, active, created_at)",
]

DOWN = [
    "DROP TABLE IF EXISTS eval_cases",
    "ALTER TABLE knowledge_candidates DROP COLUMN IF EXISTS eval_report",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
