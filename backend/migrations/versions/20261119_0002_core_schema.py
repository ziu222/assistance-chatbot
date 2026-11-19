"""core schema for step 1 (tenant, identity, channel, customer, conversation, outbox)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Mỗi phần tử một câu lệnh: asyncpg không chạy nhiều lệnh trong một prepared statement.
UP = [
    """
    CREATE TABLE tenants (
      id         uuid PRIMARY KEY,
      name       text NOT NULL,
      slug       text NOT NULL UNIQUE,
      status     text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
      locale     text NOT NULL DEFAULT 'vi',
      timezone   text NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
      created_at timestamptz NOT NULL DEFAULT now()
    )""",
    """
    CREATE TABLE users (
      id            uuid PRIMARY KEY,
      email         citext NOT NULL UNIQUE,
      password_hash text NOT NULL,
      display_name  text NOT NULL,
      status        text NOT NULL DEFAULT 'active',
      created_at    timestamptz NOT NULL DEFAULT now()
    )""",
    """
    CREATE TABLE tenant_memberships (
      tenant_id uuid NOT NULL REFERENCES tenants(id),
      user_id   uuid NOT NULL REFERENCES users(id),
      role      text NOT NULL CHECK (role IN ('owner','admin','agent')),
      status    text NOT NULL DEFAULT 'active',
      joined_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (tenant_id, user_id)
    )""",
    """
    CREATE TABLE staff_sessions (
      id          uuid PRIMARY KEY,
      tenant_id   uuid NOT NULL,
      user_id     uuid NOT NULL,
      token_hash  bytea NOT NULL UNIQUE,
      expires_at  timestamptz NOT NULL,
      created_at  timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_memberships(tenant_id, user_id)
    )""",
    """
    CREATE TABLE channel_connections (
      id                  uuid PRIMARY KEY,
      tenant_id           uuid NOT NULL REFERENCES tenants(id),
      provider            text NOT NULL CHECK (provider IN ('webchat')),
      external_account_id text,
      status              text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
      public_key          text UNIQUE,
      config              jsonb NOT NULL DEFAULT '{}',
      created_at          timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id)
    )""",
    """
    CREATE TABLE customers (
      id           uuid PRIMARY KEY,
      tenant_id    uuid NOT NULL REFERENCES tenants(id),
      display_name text NOT NULL,
      status       text NOT NULL DEFAULT 'active',
      created_at   timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id)
    )""",
    """
    CREATE TABLE channel_identities (
      id                    uuid PRIMARY KEY,
      tenant_id             uuid NOT NULL,
      customer_id           uuid NOT NULL,
      channel_connection_id uuid NOT NULL,
      provider              text NOT NULL,
      scoped_external_id    text NOT NULL,
      first_seen_at         timestamptz NOT NULL DEFAULT now(),
      last_inbound_at       timestamptz,
      FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, id),
      FOREIGN KEY (tenant_id, channel_connection_id)
        REFERENCES channel_connections(tenant_id, id),
      CONSTRAINT uq_channel_identity UNIQUE (tenant_id, channel_connection_id, scoped_external_id)
    )""",
    """
    CREATE TABLE conversations (
      id                    uuid PRIMARY KEY,
      tenant_id             uuid NOT NULL,
      channel_connection_id uuid NOT NULL,
      customer_id           uuid NOT NULL,
      status                text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
      last_sequence         bigint NOT NULL DEFAULT 0,
      last_message_at       timestamptz,
      last_message_preview  text,
      unread_count          integer NOT NULL DEFAULT 0,
      version               integer NOT NULL DEFAULT 1,
      created_at            timestamptz NOT NULL DEFAULT now(),
      updated_at            timestamptz NOT NULL DEFAULT now(),
      closed_at             timestamptz,
      FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, id),
      FOREIGN KEY (tenant_id, channel_connection_id)
        REFERENCES channel_connections(tenant_id, id),
      UNIQUE (tenant_id, id)
    )""",
    """
    CREATE UNIQUE INDEX uq_conversation_one_open
      ON conversations (tenant_id, customer_id, channel_connection_id) WHERE status = 'open'""",
    """
    CREATE INDEX ix_conversation_list
      ON conversations (tenant_id, status, last_message_at DESC, id DESC)""",
    """
    CREATE TABLE messages (
      id                    uuid PRIMARY KEY,
      tenant_id             uuid NOT NULL,
      channel_connection_id uuid NOT NULL,
      conversation_id       uuid NOT NULL,
      external_message_id   text NOT NULL,
      direction             text NOT NULL CHECK (direction IN ('inbound','outbound')),
      sender_type           text NOT NULL CHECK (sender_type IN ('customer','human','system')),
      sender_ref            text,
      message_type          text NOT NULL DEFAULT 'text' CHECK (message_type = 'text'),
      content               text NOT NULL CHECK (char_length(content) BETWEEN 1 AND 4000),
      status                text NOT NULL CHECK (status IN ('received','sent')),
      sequence              bigint NOT NULL,
      occurred_at           timestamptz NOT NULL,
      created_at            timestamptz NOT NULL DEFAULT now(),
      trace_id              text,
      FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations(tenant_id, id),
      FOREIGN KEY (tenant_id, channel_connection_id)
        REFERENCES channel_connections(tenant_id, id),
      CONSTRAINT uq_message_sequence UNIQUE (conversation_id, sequence),
      CONSTRAINT uq_message_idempotency
        UNIQUE (tenant_id, channel_connection_id, direction, external_message_id)
    )""",
    """
    CREATE TABLE outbox_events (
      id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      tenant_id      uuid NOT NULL,
      aggregate_type text NOT NULL,
      aggregate_id   uuid NOT NULL,
      event_type     text NOT NULL,
      payload        jsonb NOT NULL,
      trace_id       text,
      created_at     timestamptz NOT NULL DEFAULT now(),
      published_at   timestamptz,
      attempts       integer NOT NULL DEFAULT 0,
      last_error     text
    )""",
    "CREATE INDEX ix_outbox_unpublished ON outbox_events (id) WHERE published_at IS NULL",
]

DOWN_TABLES = [
    "outbox_events",
    "messages",
    "conversations",
    "channel_identities",
    "customers",
    "channel_connections",
    "staff_sessions",
    "tenant_memberships",
    "users",
    "tenants",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for table in DOWN_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
