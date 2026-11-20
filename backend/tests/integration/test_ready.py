from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app


def test_ready_ok(migrated_db_url):
    with TestClient(create_app(Settings(database_url=migrated_db_url))) as client:
        assert client.get("/health/ready").json() == {"status": "ready", "redis": "ok"}


def test_ready_503_when_db_down():
    dead = Settings(database_url="postgresql+asyncpg://atlas:atlas@localhost:1/atlas")
    with TestClient(create_app(dead)) as client:
        assert client.get("/health/ready").status_code == 503


def test_ready_still_200_when_redis_down(migrated_db_url):
    # Mất Redis chỉ làm chậm realtime, không được gỡ instance khỏi load balancer.
    settings = Settings(database_url=migrated_db_url, redis_url="redis://localhost:1/0")
    with TestClient(create_app(settings)) as client:
        r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "redis": "down"}
