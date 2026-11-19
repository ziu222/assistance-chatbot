import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.shared.infrastructure.db import Base, create_session_factory

# Test chạy trên PostgreSQL thật (các bất biến nằm ở index/lock, không mock được).
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://atlas:atlas@localhost:54329/atlas_test"
)
ALEMBIC_INI = Path(__file__).parents[2] / "alembic.ini"


def alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def recreate_database(url: str) -> None:
    parsed = make_url(url)
    admin = create_async_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{parsed.database}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{parsed.database}"'))
    await admin.dispose()


async def run_alembic(fn, url: str, revision: str) -> None:
    # env.py dùng asyncio.run -> phải chạy ngoài event loop đang chạy.
    await asyncio.to_thread(fn, alembic_config(url), revision)


@pytest_asyncio.fixture(scope="session")
async def migrated_db_url() -> str:
    await recreate_database(TEST_DATABASE_URL)
    await run_alembic(command.upgrade, TEST_DATABASE_URL, "head")
    return TEST_DATABASE_URL


@pytest_asyncio.fixture(scope="session")
async def engine(migrated_db_url: str):
    eng = create_async_engine(migrated_db_url)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return create_session_factory(engine)


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(engine: AsyncEngine):
    yield
    tables = [t.name for t in Base.metadata.sorted_tables]
    if tables:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
