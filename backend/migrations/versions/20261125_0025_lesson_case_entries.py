"""lesson cases tied to the entry they test; eval retry counter (design 05 L4 review fixes)

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-25
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

UP = [
    # Ca hồi quy chỉ còn hiệu lực khi mục tri thức nó kiểm tra còn đúng nội dung lúc tạo ca:
    # bài học sau hay chủ shop sửa mục đó ⇒ ca cũ tự hết hiệu lực, không chặn giá mới.
    "ALTER TABLE eval_cases ADD COLUMN entry_id uuid",
    "ALTER TABLE eval_cases ADD COLUMN entry_hash text",
    "UPDATE eval_cases e SET entry_id = c.replaces FROM knowledge_candidates c "
    "WHERE c.tenant_id = e.tenant_id AND c.id = e.candidate_id AND c.replaces IS NOT NULL",
    # Lỗi LLM ở cổng eval: thử lại có giới hạn thay vì mỗi giờ mãi mãi.
    "ALTER TABLE knowledge_candidates ADD COLUMN eval_attempts smallint NOT NULL DEFAULT 0",
]

DOWN = [
    "ALTER TABLE knowledge_candidates DROP COLUMN IF EXISTS eval_attempts",
    "ALTER TABLE eval_cases DROP COLUMN IF EXISTS entry_hash",
    "ALTER TABLE eval_cases DROP COLUMN IF EXISTS entry_id",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
