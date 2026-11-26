"""add encrypted channel credentials and outbound_deliveries queue (O5.1, ADR-0036)

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-27
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE channel_connections ADD COLUMN credentials_encrypted text",
    "CREATE UNIQUE INDEX uq_channel_connection_external "
    "ON channel_connections (provider, external_account_id) "
    "WHERE external_account_id IS NOT NULL AND status = 'active'",
    """
    CREATE TABLE outbound_deliveries (
      id                    uuid PRIMARY KEY,
      tenant_id             uuid NOT NULL REFERENCES tenants(id),
      message_id            uuid NOT NULL,
      channel_connection_id uuid NOT NULL,
      provider              text NOT NULL,
      recipient_external_id text NOT NULL,
      content               text NOT NULL,
      status                text NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','delivered','failed')),
      attempts              integer NOT NULL DEFAULT 0,
      next_attempt_at       timestamptz NOT NULL DEFAULT now(),
      provider_message_id   text,
      last_error            text,
      created_at            timestamptz NOT NULL DEFAULT now(),
      delivered_at          timestamptz,
      FOREIGN KEY (tenant_id, message_id) REFERENCES messages(tenant_id, id),
      FOREIGN KEY (tenant_id, channel_connection_id)
        REFERENCES channel_connections(tenant_id, id),
      CONSTRAINT uq_outbound_delivery_message UNIQUE (tenant_id, message_id)
    )""",
    "CREATE INDEX ix_outbound_deliveries_due "
    "ON outbound_deliveries (next_attempt_at, id) WHERE status = 'pending'",
]

DOWN = [
    "DROP INDEX IF EXISTS ix_outbound_deliveries_due",
    "DROP TABLE IF EXISTS outbound_deliveries",
    "DROP INDEX IF EXISTS uq_channel_connection_external",
    "ALTER TABLE channel_connections DROP COLUMN IF EXISTS credentials_encrypted",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
