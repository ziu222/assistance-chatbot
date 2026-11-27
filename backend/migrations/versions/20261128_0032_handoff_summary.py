"""Thêm cột tóm tắt 3 dòng và trạng thái tóm tắt cho `handoff_requests` (bước 2.8).

Revision ID: 0032
Revises: 0031
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE handoff_requests ADD COLUMN summary text")
    op.execute(
        "ALTER TABLE handoff_requests ADD COLUMN summary_status text NOT NULL DEFAULT 'pending' "
        "CHECK (summary_status IN ('pending', 'done', 'failed', 'skipped'))"
    )
    op.execute(
        "CREATE INDEX ix_handoff_summary_pending ON handoff_requests (opened_at) "
        "WHERE status IN ('open', 'claimed') AND summary_status = 'pending'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_handoff_summary_pending")
    op.execute("ALTER TABLE handoff_requests DROP COLUMN IF EXISTS summary_status")
    op.execute("ALTER TABLE handoff_requests DROP COLUMN IF EXISTS summary")
