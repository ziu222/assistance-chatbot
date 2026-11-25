"""per-intent standard eval cases and manual release eval tracking (design 06 A5, ADR-0039)

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-25
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE eval_cases DROP CONSTRAINT eval_cases_source_check",
    "ALTER TABLE eval_cases ADD CONSTRAINT eval_cases_source_check "
    "CHECK (source IN ('lesson','manual','standard'))",
    "ALTER TABLE eval_cases ADD COLUMN intent text",
    "CREATE INDEX ix_eval_cases_tenant_intent ON eval_cases (tenant_id, intent) WHERE active",
    "ALTER TABLE knowledge_releases ADD COLUMN eval_report jsonb",
    "ALTER TABLE knowledge_releases ADD COLUMN eval_attempts smallint NOT NULL DEFAULT 0",
]

DOWN = [
    "ALTER TABLE knowledge_releases DROP COLUMN IF EXISTS eval_attempts",
    "ALTER TABLE knowledge_releases DROP COLUMN IF EXISTS eval_report",
    "DROP INDEX IF EXISTS ix_eval_cases_tenant_intent",
    "ALTER TABLE eval_cases DROP COLUMN IF EXISTS intent",
    "DELETE FROM eval_cases WHERE source = 'standard'",
    "ALTER TABLE eval_cases DROP CONSTRAINT eval_cases_source_check",
    "ALTER TABLE eval_cases ADD CONSTRAINT eval_cases_source_check "
    "CHECK (source IN ('lesson','manual'))",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
