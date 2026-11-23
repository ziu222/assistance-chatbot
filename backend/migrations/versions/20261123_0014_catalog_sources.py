"""catalog sources and sync runs (step 2.13, §13.2)

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-23
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

UP = [
    # Một nguồn sở hữu mỗi sản phẩm; dữ liệu cũ đều đến từ CSV nên external_id = sku.
    "ALTER TABLE catalog_items ADD COLUMN source text NOT NULL DEFAULT 'csv' "
    "CHECK (source IN ('csv', 'sheets', 'shopee', 'tiktok'))",
    "ALTER TABLE catalog_items ADD COLUMN external_id text",
    "UPDATE catalog_items SET external_id = sku",
    "ALTER TABLE catalog_items ALTER COLUMN external_id SET NOT NULL",
    "ALTER TABLE catalog_items ADD CONSTRAINT uq_catalog_source_external "
    "UNIQUE (tenant_id, source, external_id)",
    """
    CREATE TABLE catalog_sources (            -- cấu hình nguồn ngoài (Sheets; sàn ở 2.14)
      tenant_id  uuid NOT NULL REFERENCES tenants(id),
      source     text NOT NULL CHECK (source IN ('sheets', 'shopee', 'tiktok')),
      config     jsonb NOT NULL DEFAULT '{}',  -- không chứa secret
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (tenant_id, source)
    )""",
    """
    CREATE TABLE catalog_sync_runs (
      id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      tenant_id   uuid NOT NULL REFERENCES tenants(id),
      source      text NOT NULL,
      status      text NOT NULL CHECK (status IN ('ok', 'partial', 'failed')),
      created     integer NOT NULL DEFAULT 0,
      updated     integer NOT NULL DEFAULT 0,
      deactivated integer NOT NULL DEFAULT 0,
      errors      jsonb NOT NULL DEFAULT '[]',
      started_at  timestamptz NOT NULL,
      finished_at timestamptz NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX ix_catalog_sync_runs_tenant ON catalog_sync_runs (tenant_id, source, id DESC)",
]

DOWN = [
    "DROP TABLE IF EXISTS catalog_sync_runs",
    "DROP TABLE IF EXISTS catalog_sources",
    "ALTER TABLE catalog_items DROP CONSTRAINT IF EXISTS uq_catalog_source_external",
    "ALTER TABLE catalog_items DROP COLUMN IF EXISTS external_id",
    "ALTER TABLE catalog_items DROP COLUMN IF EXISTS source",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
