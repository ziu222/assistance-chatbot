"""versioned knowledge releases and active pointer per tenant (design 05 L2, ADR-0038)

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-25
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

UP = [
    """
    CREATE TABLE knowledge_releases (
      id          uuid PRIMARY KEY,
      tenant_id   uuid NOT NULL REFERENCES tenants(id),
      number      int NOT NULL,
      snapshot    jsonb NOT NULL,  -- ảnh chụp toàn bộ FAQ/tài liệu đã duyệt (bất biến)
      reason      text NOT NULL CHECK (reason IN ('manual_edit','lesson')),
      created_by  uuid,
      eval_run_id uuid,            -- gắn ở L4 khi phát hành từ bài học
      created_at  timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, number),
      UNIQUE (tenant_id, id)
    )""",
    """
    CREATE TABLE tenant_knowledge (  -- con trỏ tới bản đang hiệu lực; quay lại = đổi con trỏ
      tenant_id          uuid PRIMARY KEY REFERENCES tenants(id),
      current_release_id uuid NOT NULL,
      updated_at         timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, current_release_id) REFERENCES knowledge_releases(tenant_id, id)
    )""",
]

DOWN = [
    "DROP TABLE IF EXISTS tenant_knowledge",
    "DROP TABLE IF EXISTS knowledge_releases",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
