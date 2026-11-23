"""order flow releases and order requests (design 03, ADR-0035)

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-24
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

UP = [
    """
    CREATE TABLE order_flow_releases (       -- bất biến; đổi cấu hình = phát hành bản mới
      id          uuid PRIMARY KEY,
      tenant_id   uuid NOT NULL REFERENCES tenants(id),
      number      int NOT NULL,
      config      jsonb NOT NULL,            -- OrderFlowConfig
      created_by  uuid,
      created_at  timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, number),
      UNIQUE (tenant_id, id)
    )""",
    """
    CREATE TABLE tenant_order_flow (         -- con trỏ tới bản đang dùng; khôi phục = đổi con trỏ
      tenant_id          uuid PRIMARY KEY REFERENCES tenants(id),
      current_release_id uuid NOT NULL,
      updated_at         timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, current_release_id) REFERENCES order_flow_releases(tenant_id, id)
    )""",
    """
    CREATE TABLE order_requests (
      id              uuid PRIMARY KEY,
      tenant_id       uuid NOT NULL,
      conversation_id uuid NOT NULL,
      status          text NOT NULL DEFAULT 'collecting'
                      CHECK (status IN ('collecting','ready','confirmed','cancelled','expired')),
      flow_release_id uuid NOT NULL,
      channel         text NOT NULL,
      items           jsonb NOT NULL DEFAULT '[]',   -- sku, variant, quantity, unit_price_vnd từ catalog
      subtotal_vnd    bigint NOT NULL DEFAULT 0,      -- tạm tính, chưa gồm phí ship
      customer_fields jsonb NOT NULL DEFAULT '{}',    -- DỮ LIỆU CÁ NHÂN: chỉ nằm ở đây
      unverified      text[] NOT NULL DEFAULT '{}',   -- trường do model trích, chưa ai xác minh
      missing_fields  text[] NOT NULL DEFAULT '{}',
      created_by      text NOT NULL DEFAULT 'bot' CHECK (created_by IN ('bot','staff')),
      confirmed_by    uuid,
      opened_at       timestamptz NOT NULL DEFAULT now(),
      updated_at      timestamptz NOT NULL DEFAULT now(),
      ready_at        timestamptz,
      closed_at       timestamptz,
      pii_purged_at   timestamptz,
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id),
      FOREIGN KEY (tenant_id, flow_release_id) REFERENCES order_flow_releases(tenant_id, id)
    )""",
    # Một phiếu đang mở mỗi hội thoại, như ticket nhờ nhân viên.
    """
    CREATE UNIQUE INDEX uq_order_request_open ON order_requests (tenant_id, conversation_id)
      WHERE status IN ('collecting','ready')""",
    "CREATE INDEX ix_order_requests_status ON order_requests (tenant_id, status, updated_at)",
    "ALTER TABLE handoff_requests DROP CONSTRAINT handoff_requests_source_check",
    "ALTER TABLE handoff_requests ADD CONSTRAINT handoff_requests_source_check "
    "CHECK (source IN ('customer_button','phrase','topic','staff','order'))",
    "ALTER TABLE handoff_requests ADD COLUMN order_request_id uuid",
]

DOWN = [
    "ALTER TABLE handoff_requests DROP COLUMN IF EXISTS order_request_id",
    "DELETE FROM handoff_requests WHERE source = 'order'",
    "ALTER TABLE handoff_requests DROP CONSTRAINT handoff_requests_source_check",
    "ALTER TABLE handoff_requests ADD CONSTRAINT handoff_requests_source_check "
    "CHECK (source IN ('customer_button','phrase','topic','staff'))",
    "DROP TABLE IF EXISTS order_requests",
    "DROP TABLE IF EXISTS tenant_order_flow",
    "DROP TABLE IF EXISTS order_flow_releases",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
