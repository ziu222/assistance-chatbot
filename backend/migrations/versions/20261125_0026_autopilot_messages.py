"""allow bot sender_type and draft_id on messages (design 06 A1, ADR-0039)

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-25
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE messages DROP CONSTRAINT messages_sender_type_check",
    "ALTER TABLE messages ADD CONSTRAINT messages_sender_type_check "
    "CHECK (sender_type IN ('customer','human','system','bot'))",
    "ALTER TABLE messages ADD COLUMN draft_id uuid",
    "CREATE INDEX ix_messages_draft ON messages (tenant_id, draft_id) WHERE draft_id IS NOT NULL",
]

DOWN = [
    "DROP INDEX IF EXISTS ix_messages_draft",
    "ALTER TABLE messages DROP COLUMN IF EXISTS draft_id",
    "UPDATE messages SET sender_type = 'system' WHERE sender_type = 'bot'",
    "ALTER TABLE messages DROP CONSTRAINT messages_sender_type_check",
    "ALTER TABLE messages ADD CONSTRAINT messages_sender_type_check "
    "CHECK (sender_type IN ('customer','human','system'))",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
