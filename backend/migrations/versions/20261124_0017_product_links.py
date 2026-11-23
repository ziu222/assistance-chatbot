"""product links on catalog items and order destination (design 03 O4)

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-24
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

UP = [
    # Trang sản phẩm (website hoặc sàn); adapter nguồn điền, nhập tay có thể bỏ trống.
    "ALTER TABLE catalog_items ADD COLUMN url text CHECK (url IS NULL OR url LIKE 'https://%')",
    # Cách hướng khách lúc ghi phiếu: gửi link thì không cần nhân viên xác nhận.
    "ALTER TABLE order_requests ADD COLUMN destination text NOT NULL DEFAULT 'staff_confirms'",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE order_requests DROP COLUMN IF EXISTS destination")
    op.execute("ALTER TABLE catalog_items DROP COLUMN IF EXISTS url")
