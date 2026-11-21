"""agents from templates, immutable agent versions, tenant AI kill switch (step 2.2)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-21
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UP = [
    """
    CREATE TABLE tenant_ai_settings (
      tenant_id            uuid PRIMARY KEY REFERENCES tenants(id),
      ai_enabled           boolean NOT NULL DEFAULT false,          -- kill switch, mặc định TẮT
      daily_cost_cap_micro bigint  NOT NULL DEFAULT 2000000 CHECK (daily_cost_cap_micro >= 0),
      updated_at           timestamptz NOT NULL DEFAULT now()
    )""",
    """
    CREATE TABLE agents (
      id                    uuid PRIMARY KEY,
      tenant_id             uuid NOT NULL REFERENCES tenants(id),
      channel_connection_id uuid NOT NULL,
      name                  text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
      template_key          text NOT NULL,
      template_version      int  NOT NULL,
      mode                  text NOT NULL DEFAULT 'manual' CHECK (mode IN ('manual','shadow')),
      shadow_sample_rate    numeric(3,2) NOT NULL DEFAULT 1.00
                              CHECK (shadow_sample_rate BETWEEN 0 AND 1),
      current_version_id    uuid,                -- trỏ agent_versions; đặt cùng transaction
      version               int  NOT NULL DEFAULT 1,  -- optimistic concurrency
      created_at            timestamptz NOT NULL DEFAULT now(),
      updated_at            timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id),
      UNIQUE (tenant_id, channel_connection_id),   -- bước 2: một agent mỗi kênh
      FOREIGN KEY (tenant_id, channel_connection_id) REFERENCES channel_connections(tenant_id, id)
    )""",
    """
    CREATE TABLE agent_versions (
      id             uuid PRIMARY KEY,
      tenant_id      uuid NOT NULL,
      agent_id       uuid NOT NULL,
      version_number int  NOT NULL CHECK (version_number >= 1),
      config         jsonb NOT NULL,              -- ảnh chụp template: bất biến
      created_by     uuid,
      created_at     timestamptz NOT NULL DEFAULT now(),
      UNIQUE (agent_id, version_number),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, agent_id) REFERENCES agents(tenant_id, id)
    )""",
]

DOWN = ["DROP TABLE IF EXISTS agent_versions", "DROP TABLE IF EXISTS agents",
        "DROP TABLE IF EXISTS tenant_ai_settings"]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
