"""writing assistant requests and brand profiles (step 2.11)

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-23
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

UP = [
    # Yêu cầu gợi ý viết: API ghi, agent worker (nơi duy nhất có key LLM) xử lý.
    """
    CREATE TABLE writing_requests (
      id           uuid PRIMARY KEY,
      tenant_id    uuid NOT NULL REFERENCES tenants(id),
      kind         text NOT NULL CHECK (kind IN ('auto_message','faq','policy','other')),
      level        text NOT NULL CHECK (level IN ('fix','polish','brand','alternatives')),
      text         text NOT NULL CHECK (char_length(text) BETWEEN 1 AND 2000),
      status       text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','running','done','failed')),
      suggestions  jsonb,                -- [{text, changes, fact_check}]
      error_code   text,
      lease_until  timestamptz,
      created_by   uuid,
      created_at   timestamptz NOT NULL DEFAULT now(),
      finished_at  timestamptz,
      UNIQUE (tenant_id, id)
    )""",
    "CREATE INDEX ix_writing_pending ON writing_requests (created_at) WHERE status = 'pending'",
    # Hồ sơ thương hiệu có version: mỗi lần lưu là một bản mới; bản hiện hành = số lớn nhất.
    """
    CREATE TABLE brand_profiles (
      id          uuid PRIMARY KEY,
      tenant_id   uuid NOT NULL REFERENCES tenants(id),
      number      int NOT NULL,
      config      jsonb NOT NULL,
      created_by  uuid,
      created_at  timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, number)
    )""",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for t in ("brand_profiles", "writing_requests"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
