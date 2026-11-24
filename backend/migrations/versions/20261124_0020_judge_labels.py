"""owner labels for judged shadow comparisons (design 04 S3, ADR-0037)

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-24
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE judge_labels (
          comparison_id uuid PRIMARY KEY,       -- gán lại thì ghi đè: nhãn mới nhất là nhãn đúng
          tenant_id     uuid NOT NULL,
          label         text NOT NULL CHECK (label IN ('sendable','rewrite','wrong')),
          labeled_by    uuid NOT NULL,
          labeled_at    timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (tenant_id, comparison_id) REFERENCES shadow_comparisons(tenant_id, id)
        )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS judge_labels")
