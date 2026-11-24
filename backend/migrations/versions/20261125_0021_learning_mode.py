"""learning mode: drafts shown to staff and what staff did with them (design 05 L1, ADR-0038)

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-25
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE agents DROP CONSTRAINT agents_mode_check",
    "ALTER TABLE agents ADD CONSTRAINT agents_mode_check "
    "CHECK (mode IN ('manual','shadow','learning'))",
    # Lần đầu nháp được hiện cho nhân viên: bước 3 tách cặp "đã thấy" khỏi cặp "mù".
    "ALTER TABLE agent_drafts ADD COLUMN shown_at timestamptz",
    """
    CREATE TABLE draft_uses (
      draft_id        uuid PRIMARY KEY,           -- mỗi nháp một lựa chọn
      tenant_id       uuid NOT NULL,
      conversation_id uuid NOT NULL,
      message_id      uuid,                       -- tin nhân viên đã gửi; NULL khi bấm Bỏ
      action          text NOT NULL CHECK (action IN ('as_is','edited','discarded')),
      edit_ratio      real NOT NULL,
      diff_features   jsonb NOT NULL DEFAULT '{}', -- số tiền/link/câu thêm bớt: không chép chữ
      user_id         uuid NOT NULL,
      created_at      timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id)
    )""",
    "CREATE INDEX ix_draft_uses_tenant ON draft_uses (tenant_id, created_at)",
]

DOWN = [
    "DROP TABLE IF EXISTS draft_uses",
    "ALTER TABLE agent_drafts DROP COLUMN IF EXISTS shown_at",
    "UPDATE agents SET mode = 'shadow' WHERE mode = 'learning'",
    "ALTER TABLE agents DROP CONSTRAINT agents_mode_check",
    "ALTER TABLE agents ADD CONSTRAINT agents_mode_check CHECK (mode IN ('manual','shadow'))",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
