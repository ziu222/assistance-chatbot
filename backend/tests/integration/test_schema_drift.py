"""ORM model và migration phải khớp cột. Chỉ kiểm tên cột và nullable (model còn mỏng có chủ ý)."""

from sqlalchemy import text

import app.shared.infrastructure.model_registry  # noqa: F401
from app.shared.infrastructure.db import Base


async def test_models_match_database_columns(engine):
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
        )
        db = {(t, c): n == "YES" for t, c, n in rows}

    assert Base.metadata.tables, "model registry empty"
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            key = (table.name, col.name)
            assert key in db, f"{key} missing in database"
            assert db[key] == col.nullable, f"{key} nullable mismatch"
        db_cols = {c for t, c in db if t == table.name}
        assert db_cols == {c.name for c in table.columns}, f"{table.name} column set differs"
