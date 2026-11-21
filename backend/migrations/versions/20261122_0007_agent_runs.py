"""agent runs, run steps and drafts (step 2.5)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-22
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

UP = [
    """
    CREATE TABLE agent_runs (
      id                 uuid PRIMARY KEY,
      tenant_id          uuid NOT NULL,
      agent_id           uuid NOT NULL,
      agent_version_id   uuid NOT NULL,
      conversation_id    uuid NOT NULL,
      from_sequence      bigint NOT NULL,
      to_sequence        bigint NOT NULL,
      status             text NOT NULL CHECK (status IN ('running','completed','handoff_suggested',
                                               'skipped','superseded','failed','timed_out')),
      skip_reason        text,
      error_code         text,
      intents            text[],
      knowledge_hash     text NOT NULL,
      prompt_version     text NOT NULL,
      model_calls        int NOT NULL DEFAULT 0,
      tool_calls         int NOT NULL DEFAULT 0,
      input_tokens       int NOT NULL DEFAULT 0,
      cache_read_tokens  int NOT NULL DEFAULT 0,
      cache_write_tokens int NOT NULL DEFAULT 0,
      output_tokens      int NOT NULL DEFAULT 0,
      cost_micro         bigint NOT NULL DEFAULT 0,
      started_at         timestamptz NOT NULL DEFAULT now(),
      finished_at        timestamptz,
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, agent_id) REFERENCES agents(tenant_id, id),
      FOREIGN KEY (tenant_id, agent_version_id) REFERENCES agent_versions(tenant_id, id),
      FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id)
    )""",
    "CREATE INDEX ix_agent_runs_conversation ON agent_runs (tenant_id, conversation_id, started_at)",
    "CREATE INDEX ix_agent_runs_running ON agent_runs (conversation_id) WHERE status = 'running'",
    """
    CREATE TABLE agent_run_steps (
      id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      tenant_id  uuid NOT NULL,
      run_id     uuid NOT NULL,
      step_index int NOT NULL,
      kind       text NOT NULL CHECK (kind IN ('rule','classify','llm','tool','validate')),
      name       text,
      status     text NOT NULL,
      latency_ms int,
      detail     jsonb NOT NULL DEFAULT '{}',   -- đã chiếu: KHÔNG lưu prompt đầy đủ
      UNIQUE (run_id, step_index),
      FOREIGN KEY (tenant_id, run_id) REFERENCES agent_runs(tenant_id, id)
    )""",
    """
    CREATE TABLE agent_drafts (
      id                uuid PRIMARY KEY,
      tenant_id         uuid NOT NULL,
      run_id            uuid NOT NULL UNIQUE,
      conversation_id   uuid NOT NULL,
      based_on_sequence bigint NOT NULL,
      text              text NOT NULL CHECK (char_length(text) BETWEEN 1 AND 4000),
      sources           jsonb NOT NULL,         -- [{"type":"product","id":"..."}]
      grounded          boolean NOT NULL,
      needs_human       boolean NOT NULL,
      reason            text,
      created_at        timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, run_id) REFERENCES agent_runs(tenant_id, id),
      FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id)
    )""",
    "CREATE INDEX ix_agent_drafts_conversation ON agent_drafts (tenant_id, conversation_id)",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for t in ("agent_drafts", "agent_run_steps", "agent_runs"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
