"""allow messenger & zalo_oa channel providers and catalog freshness threshold (design 06 A6)

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-25
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE channel_connections DROP CONSTRAINT channel_connections_provider_check",
    "ALTER TABLE channel_connections ADD CONSTRAINT channel_connections_provider_check "
    "CHECK (provider IN ('webchat','messenger','zalo_oa'))",
    "ALTER TABLE tenant_policy ADD COLUMN catalog_max_age_hours integer NOT NULL DEFAULT 24 "
    "CHECK (catalog_max_age_hours BETWEEN 1 AND 720)",
]

DOWN = [
    "ALTER TABLE tenant_policy DROP COLUMN IF EXISTS catalog_max_age_hours",
    "UPDATE channel_connections SET provider = 'webchat' WHERE provider <> 'webchat'",
    "ALTER TABLE channel_connections DROP CONSTRAINT channel_connections_provider_check",
    "ALTER TABLE channel_connections ADD CONSTRAINT channel_connections_provider_check "
    "CHECK (provider IN ('webchat'))",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
