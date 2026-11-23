"""order requests: fields staff edited (design 03 O3)

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-24
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Trường nhân viên đã sửa trước khi xác nhận: nguồn cho gợi ý chỉnh quy trình (§4.3).
    op.execute("ALTER TABLE order_requests ADD COLUMN staff_edited text[] NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE order_requests DROP COLUMN IF EXISTS staff_edited")
