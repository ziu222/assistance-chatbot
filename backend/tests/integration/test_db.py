from sqlalchemy import text


async def test_select_one(engine):
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1


async def test_session_factory_roundtrip(session_factory):
    async with session_factory() as session:
        assert (await session.execute(text("SELECT 2"))).scalar_one() == 2


async def test_citext_extension_installed(engine):
    async with engine.connect() as conn:
        found = await conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'citext'"))
        assert found.scalar_one_or_none() == 1
