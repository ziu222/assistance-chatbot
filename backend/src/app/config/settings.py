from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://atlas:atlas@localhost:54329/atlas"
    redis_url: str = "redis://localhost:6379/0"

    # Origin phục vụ trang iframe /w/{key}; widget gọi API từ chính origin này.
    public_origin: str = "http://localhost:8000"
    # JSON {"kid": "secret"}; không có mặc định để không bao giờ chạy với secret mẫu.
    webchat_token_secrets: dict[str, str] = {}
    webchat_token_kid: str = "k1"
    webchat_token_ttl_seconds: int = 7 * 24 * 3600

    # Bundle widget đã build (loader.js, app.js, app.css), phục vụ tại /widget/.
    widget_dist_dir: str = str(Path(__file__).resolve().parents[4] / "widget" / "dist")

    # Origin của admin inbox; dùng kiểm tra CSRF cho method không an toàn.
    staff_origin: str = "http://localhost:5173"
    staff_session_ttl_seconds: int = 12 * 3600  # hết hạn tuyệt đối


@lru_cache
def get_settings() -> Settings:
    return Settings()
