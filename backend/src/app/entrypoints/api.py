"""Chạy API: `uv run uvicorn app.entrypoints.api:app --port 8000`. Log JSON một dòng mỗi bản ghi."""

from app.main import create_app
from app.shared.infrastructure.logging import configure_logging

configure_logging()
app = create_app()
