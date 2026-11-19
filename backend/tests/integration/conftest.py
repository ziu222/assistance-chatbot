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

from app.shared.infrastructure.db import create_session_factory

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
    async with engine.begin() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        names = [r[0] for r in rows]
        if names:
            await conn.execute(text(f"TRUNCATE {', '.join(names)} RESTART IDENTITY CASCADE"))


TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
TEST_SETTINGS = {
    "redis_url": TEST_REDIS_URL,
    "public_origin": "http://widget.test",
    "staff_origin": "https://inbox.test",
    "webchat_token_secrets": {"k1": "s" * 32},
    "webchat_token_kid": "k1",
}


@pytest_asyncio.fixture
async def app_client(migrated_db_url):
    """Factory: app chạy lifespan thật trong cùng event loop, gọi qua ASGI (không mở cổng)."""
    import httpx
    import redis.asyncio as aioredis

    from app.config.settings import Settings
    from app.main import create_app

    r = aioredis.from_url(TEST_REDIS_URL)
    await r.flushdb()  # bộ đếm rate limit không rò giữa các test
    await r.aclose()
    opened = []

    async def factory(base_url: str, **over):
        settings = Settings(**({"database_url": migrated_db_url} | TEST_SETTINGS | over))
        app = create_app(settings)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)
        opened.append((client, lifespan))
        return client

    yield factory
    for client, lifespan in opened:
        await client.aclose()
        await lifespan.__aexit__(None, None, None)
