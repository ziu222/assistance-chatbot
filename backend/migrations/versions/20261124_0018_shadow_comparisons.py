"""shadow comparisons: drafts paired with what staff sent (design 04 S1, ADR-0037)

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-24
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

UP = [
    """
    CREATE TABLE shadow_comparisons (
      id                 uuid PRIMARY KEY,
      tenant_id          uuid NOT NULL,
      conversation_id    uuid NOT NULL,
      draft_id           uuid NOT NULL UNIQUE,      -- một nháp, một lần so sánh
      run_id             uuid NOT NULL,
      status             text NOT NULL CHECK (status IN ('paired','superseded','unanswered')),
      draft_exposure     text NOT NULL DEFAULT 'hidden' CHECK (draft_exposure IN ('hidden','shown')),
      reference_from_seq bigint,                    -- tin nhân viên: chỉ số thứ tự, không chép nội dung
      reference_to_seq   bigint,
      intent             text,
      config_version     jsonb NOT NULL,            -- template, prompt, model, knowledge_hash
      code_checks        jsonb,
      code_verdict       text CHECK (code_verdict IN ('not_sendable','wrong')),
      bot_lead_seconds   integer,                   -- nhân viên trả lời sau nháp bao lâu (âm = trước)
      created_at         timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id)
    )""",
    "CREATE INDEX ix_shadow_comparisons_tenant ON shadow_comparisons (tenant_id, created_at)",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS shadow_comparisons")
