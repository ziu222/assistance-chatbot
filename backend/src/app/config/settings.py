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

    # Cổng /metrics nội bộ của relay worker (không mở ra Internet).
    worker_metrics_port: int = 9101

    # Hàng đợi agent (ADR-0008). Giá trị khởi điểm, hiệu chỉnh bằng đo đạc.
    agent_debounce_window_seconds: float = 6.0  # chờ thêm sau mỗi tin khách
    agent_debounce_max_wait_seconds: float = 20.0  # trần tính từ tin đầu của cụm
    agent_lease_seconds: float = 120.0
    agent_max_attempts: int = 3
    agent_worker_concurrency: int = 4
    agent_worker_metrics_port: int = 9102

    # LLM (ADR-0015). Thiếu key thì provider đó rời khỏi route; thiếu cả hai thì lỗi lúc khởi động.
    claude_api_key: str = ""
    openai_api_key: str = ""
    # Rỗng = API chính thức. Gateway thử nghiệm chỉ khác giá trị này, không có code riêng.
    llm_base_url: str = ""
    # Tắt tạm một provider khi nó sập kéo dài, vd "anthropic" (phân tách bằng dấu phẩy).
    llm_disabled_providers: str = ""
    # Trần chi phí ngày của trợ lý viết, riêng với trần của nháp (micro-USD; 300000 = 0,3 USD).
    authoring_daily_cap_micro: int = 300_000
    # Giám khảo so sánh shadow (thiết kế 04 §6.2): trần riêng mỗi tenant/ngày và lấy mẫu.
    judge_daily_cap_micro: int = 200_000
    judge_full_per_day: int = 100  # chấm mọi cặp tới mức này mỗi ngày
    judge_per_intent_after: int = 10  # quá mức trên: mỗi ý định chấm thêm tối đa chừng này
    # Dựng ứng viên tri thức và eval bài học (thiết kế 05 §6.2): trần riêng mỗi tenant/ngày.
    learning_daily_cap_micro: int = 200_000

    # Google Sheets (§13.2): JSON key của service account, chỉ quyền đọc. Rỗng = tắt nguồn Sheets.
    google_service_account_json: str = ""
    catalog_sync_interval_minutes: int = 60  # lịch sync tự động các nguồn ngoài


@lru_cache
def get_settings() -> Settings:
    return Settings()
