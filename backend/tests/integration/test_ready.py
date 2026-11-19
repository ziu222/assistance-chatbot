from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app


def test_ready_ok(migrated_db_url):
    with TestClient(create_app(Settings(database_url=migrated_db_url))) as client:
        assert client.get("/health/ready").json() == {"status": "ready"}


def test_ready_503_when_db_down():
    dead = Settings(database_url="postgresql+asyncpg://atlas:atlas@localhost:1/atlas")
    with TestClient(create_app(dead)) as client:
        assert client.get("/health/ready").status_code == 503
