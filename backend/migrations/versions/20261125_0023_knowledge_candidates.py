"""knowledge candidates built from staff draft edits (design 05 L3, ADR-0038)

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-25
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

UP = [
    """
    CREATE TABLE knowledge_candidates (
      id            uuid PRIMARY KEY,
      tenant_id     uuid NOT NULL REFERENCES tenants(id),
      kind          text NOT NULL CHECK (
        kind IN ('faq_new','faq_update','doc_section','brand_example','order_flow')
      ),
      intent        text NOT NULL,
      topic         text NOT NULL,
      fingerprint   text NOT NULL,       -- chống đề xuất lặp lại cùng nội dung (30 ngày)
      proposal      jsonb NOT NULL,      -- nội dung đề xuất đã xóa dữ liệu cá nhân
      replaces      uuid,                -- id FAQ/tài liệu bị thay thế nếu faq_update
      evidence      jsonb NOT NULL,      -- danh sách id draft_uses/message_id làm bằng chứng
      checks        jsonb NOT NULL,      -- kết quả kiểm tra sự thật, PII, chữ khách bằng code
      status        text NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending','approved','eval_failed','published','rejected')
      ),
      reject_reason text,
      reviewed_by   uuid,
      reviewed_at   timestamptz,
      release_id    uuid,
      created_at    timestamptz NOT NULL DEFAULT now(),
      updated_at    timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id)
    )""",
    "CREATE INDEX ix_knowledge_candidates_tenant_status "
    "ON knowledge_candidates (tenant_id, status, created_at DESC)",
    "CREATE INDEX ix_knowledge_candidates_fingerprint "
    "ON knowledge_candidates (tenant_id, fingerprint, created_at DESC)",
]

DOWN = [
    "DROP TABLE IF EXISTS knowledge_candidates",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
