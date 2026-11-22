"""allow escalation signal steps in agent run traces (step 2.10)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-23
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

KINDS_OLD = "'rule','classify','llm','tool','validate'"


def upgrade() -> None:
    op.execute("ALTER TABLE agent_run_steps DROP CONSTRAINT agent_run_steps_kind_check")
    op.execute(
        "ALTER TABLE agent_run_steps ADD CONSTRAINT agent_run_steps_kind_check "
        f"CHECK (kind IN ({KINDS_OLD},'signal'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM agent_run_steps WHERE kind = 'signal'")
    op.execute("ALTER TABLE agent_run_steps DROP CONSTRAINT agent_run_steps_kind_check")
    op.execute(
        "ALTER TABLE agent_run_steps ADD CONSTRAINT agent_run_steps_kind_check "
        f"CHECK (kind IN ({KINDS_OLD}))"
    )
