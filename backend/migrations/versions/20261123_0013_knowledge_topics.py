"""topics on FAQ entries and docs for knowledge coverage (step 2.13)

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-23
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# NULL = để hệ thống đoán chủ đề từ nội dung; mảng (kể cả rỗng) = chủ shop đã chọn (§13.3).
UP = [
    "ALTER TABLE faq_entries ADD COLUMN topics text[]",
    "ALTER TABLE knowledge_docs ADD COLUMN topics text[]",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge_docs DROP COLUMN IF EXISTS topics")
    op.execute("ALTER TABLE faq_entries DROP COLUMN IF EXISTS topics")
