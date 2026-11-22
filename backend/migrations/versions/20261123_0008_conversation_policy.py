"""conversation handling state, policy releases and decisions (step 2.7)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-23
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UP = [
    """
    ALTER TABLE conversations ADD COLUMN handling text NOT NULL DEFAULT 'bot'
      CHECK (handling IN ('bot','waiting_human','human'))""",
    """
    CREATE TABLE policy_releases (
      id          uuid PRIMARY KEY,
      tenant_id   uuid NOT NULL REFERENCES tenants(id),
      number      int NOT NULL,
      config      jsonb NOT NULL,          -- PolicyConfig: giờ làm việc + tình huống (bất biến)
      created_by  uuid,
      created_at  timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, number),
      UNIQUE (tenant_id, id)
    )""",
    """
    CREATE TABLE tenant_policy (            -- con trỏ tới release hiện hành; đổi = phát hành/khôi phục
      tenant_id          uuid PRIMARY KEY REFERENCES tenants(id),
      current_release_id uuid NOT NULL,
      updated_at         timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, current_release_id) REFERENCES policy_releases(tenant_id, id)
    )""",
    """
    CREATE TABLE policy_decisions (
      id                uuid PRIMARY KEY,
      tenant_id         uuid NOT NULL,
      conversation_id   uuid NOT NULL,
      to_sequence       bigint NOT NULL,      -- cụm tin đã gom mà quyết định này áp dụng
      policy_release_id uuid NOT NULL,
      situation         text CHECK (situation IN ('paused','after_hours','welcome')),
      canned_text       text,                 -- đã thay biến; 2.7 chỉ ghi, chưa gửi
      handling_before   text NOT NULL,
      set_handling      text,
      run_agent         boolean NOT NULL,
      sent              boolean NOT NULL DEFAULT false,
      created_at        timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, conversation_id, to_sequence),   -- worker retry không quyết định hai lần
      FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id),
      FOREIGN KEY (tenant_id, policy_release_id) REFERENCES policy_releases(tenant_id, id)
    )""",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for t in ("policy_decisions", "tenant_policy", "policy_releases"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS handling")
