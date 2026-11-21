"""knowledge: approved FAQ, small docs, structured catalog with trigram search (step 2.3)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-21
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UP = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    """
    CREATE TABLE faq_entries (
      id         uuid PRIMARY KEY,
      tenant_id  uuid NOT NULL REFERENCES tenants(id),
      question   text NOT NULL CHECK (char_length(question) BETWEEN 1 AND 500),
      answer     text NOT NULL CHECK (char_length(answer) BETWEEN 1 AND 4000),
      status     text NOT NULL DEFAULT 'approved' CHECK (status IN ('draft','approved','archived')),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id)
    )""",
    """
    CREATE TABLE knowledge_docs (
      id         uuid PRIMARY KEY,
      tenant_id  uuid NOT NULL REFERENCES tenants(id),
      title      text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
      body       text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 20000),
      status     text NOT NULL DEFAULT 'approved' CHECK (status IN ('draft','approved','archived')),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id)
    )""",
    """
    CREATE TABLE catalog_items (
      id          uuid PRIMARY KEY,
      tenant_id   uuid NOT NULL REFERENCES tenants(id),
      sku         text NOT NULL CHECK (char_length(sku) BETWEEN 1 AND 64),
      name        text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
      description text,
      price_vnd   bigint NOT NULL CHECK (price_vnd >= 0),
      variants    jsonb NOT NULL DEFAULT '[]',
      active      boolean NOT NULL DEFAULT true,
      search_text text NOT NULL,                 -- tên + mô tả đã bỏ dấu, chữ thường (Python)
      synced_at   timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, sku),
      UNIQUE (tenant_id, id)
    )""",
    "CREATE INDEX ix_catalog_search ON catalog_items USING gin (search_text gin_trgm_ops)",
]

DOWN = [
    "DROP TABLE IF EXISTS catalog_items",
    "DROP TABLE IF EXISTS knowledge_docs",
    "DROP TABLE IF EXISTS faq_entries",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
