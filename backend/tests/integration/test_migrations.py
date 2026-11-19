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


async def test_upgrade_downgrade_upgrade():
    await recreate_database(MIGRATIONS_DB_URL)

    await run_alembic(command.upgrade, MIGRATIONS_DB_URL, "head")
    assert await _has_citext(MIGRATIONS_DB_URL)

    await run_alembic(command.downgrade, MIGRATIONS_DB_URL, "base")
    assert not await _has_citext(MIGRATIONS_DB_URL)

    await run_alembic(command.upgrade, MIGRATIONS_DB_URL, "head")
    assert await _has_citext(MIGRATIONS_DB_URL)
