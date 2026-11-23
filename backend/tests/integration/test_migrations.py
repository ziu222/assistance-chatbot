from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from .conftest import TEST_DATABASE_URL, recreate_database, run_alembic

MIGRATIONS_DB_URL = (
    make_url(TEST_DATABASE_URL)
    .set(database="atlas_test_migrations")
    .render_as_string(hide_password=False)
)


async def _has_citext(url: str) -> bool:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            found = await conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'citext'"))
            return found.scalar_one_or_none() == 1
    finally:
        await engine.dispose()


async def _tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            return {r[0] for r in rows} - {"alembic_version"}
    finally:
        await engine.dispose()


CORE_TABLES = {
    "tenants",
    "users",
    "tenant_memberships",
    "staff_sessions",
    "channel_connections",
    "customers",
    "channel_identities",
    "conversations",
    "messages",
    "outbox_events",
    "conversation_run_requests",
    "policy_releases",
    "tenant_policy",
    "policy_decisions",
    "handoff_requests",
    "learning_signals",
    "writing_requests",
    "brand_profiles",
    "tenant_ai_settings",
    "agents",
    "agent_versions",
    "faq_entries",
    "knowledge_docs",
    "catalog_items",
    "usage_events",
    "agent_runs",
    "agent_run_steps",
    "agent_drafts",
}


async def test_upgrade_downgrade_upgrade():
    await recreate_database(MIGRATIONS_DB_URL)

    await run_alembic(command.upgrade, MIGRATIONS_DB_URL, "head")
    assert await _has_citext(MIGRATIONS_DB_URL)
    assert await _tables(MIGRATIONS_DB_URL) == CORE_TABLES

    await run_alembic(command.downgrade, MIGRATIONS_DB_URL, "base")
    assert not await _has_citext(MIGRATIONS_DB_URL)
    assert await _tables(MIGRATIONS_DB_URL) == set()

    await run_alembic(command.upgrade, MIGRATIONS_DB_URL, "head")
    assert await _has_citext(MIGRATIONS_DB_URL)
    assert await _tables(MIGRATIONS_DB_URL) == CORE_TABLES
