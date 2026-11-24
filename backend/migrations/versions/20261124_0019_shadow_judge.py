"""shadow comparisons: LLM judge results (design 04 S2, ADR-0037)

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-24
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE shadow_comparisons ADD COLUMN judge jsonb",  # JudgeOut
    "ALTER TABLE shadow_comparisons ADD COLUMN judge_model text",
    "ALTER TABLE shadow_comparisons ADD COLUMN judge_prompt_version text",
    "ALTER TABLE shadow_comparisons ADD COLUMN judge_cost_micro bigint",
    "ALTER TABLE shadow_comparisons ADD COLUMN judge_attempts smallint NOT NULL DEFAULT 0",
    # daily_cap | sampled_out | refusal | gave_up: không chấm nữa (vẫn có kết quả của code)
    "ALTER TABLE shadow_comparisons ADD COLUMN judge_skipped text",
    "ALTER TABLE shadow_comparisons ADD COLUMN judged_at timestamptz",
    "ALTER TABLE shadow_comparisons ADD COLUMN final_verdict text",
    "ALTER TABLE shadow_comparisons ADD COLUMN sendable text",
    "ALTER TABLE shadow_comparisons ADD COLUMN rationale_purged_at timestamptz",
    # Cặp code đã kết luận: kết luận cuối có ngay, không cần giám khảo.
    "UPDATE shadow_comparisons SET final_verdict = code_verdict, sendable = 'must_not_send' "
    "WHERE code_verdict IS NOT NULL",
    "CREATE INDEX ix_shadow_to_judge ON shadow_comparisons (created_at) "
    "WHERE status = 'paired' AND code_verdict IS NULL AND judged_at IS NULL "
    "AND judge_skipped IS NULL",
]

DOWN = [
    "DROP INDEX IF EXISTS ix_shadow_to_judge",
    *(
        f"ALTER TABLE shadow_comparisons DROP COLUMN IF EXISTS {c}"
        for c in (
            "rationale_purged_at", "sendable", "final_verdict", "judged_at", "judge_skipped",
            "judge_attempts", "judge_cost_micro", "judge_prompt_version", "judge_model", "judge",
        )
    ),
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWN:
        op.execute(stmt)
