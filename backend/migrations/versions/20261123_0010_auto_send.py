"""auto message sending switch and send outcome (step 2.9)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-23
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

UP = [
    # Công tắc riêng, mặc định TẮT (giống kill switch AI): lưu cấu hình không tự bật gửi.
    "ALTER TABLE tenant_policy ADD COLUMN auto_send boolean NOT NULL DEFAULT false",
    # Vì sao tin tự động KHÔNG được gửi (SendGate từ chối); NULL khi đã gửi hoặc không có tin.
    "ALTER TABLE policy_decisions ADD COLUMN send_block text",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE policy_decisions DROP COLUMN IF EXISTS send_block")
    op.execute("ALTER TABLE tenant_policy DROP COLUMN IF EXISTS auto_send")
