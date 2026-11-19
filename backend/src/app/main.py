import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text

from app.config.settings import Settings, get_settings
from app.modules.identity.presentation.http.auth import router as staff_auth
from app.modules.inbox.presentation.http.staff_inbox import router as staff_inbox
from app.modules.integration.infrastructure.webchat.token import TokenSigner
from app.modules.integration.presentation.http.public_webchat import router as public_webchat
from app.shared.infrastructure.db import create_engine, create_session_factory
from app.shared.infrastructure.rate_limit import RateLimiter
from app.shared.presentation import http_errors

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        redis = aioredis.from_url(
            settings.redis_url, socket_connect_timeout=1, socket_timeout=1, retry=None
        )
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.rate_limiter = RateLimiter(redis)
        app.state.token_signer = None
        if settings.webchat_token_secrets:
            # Secret sai (ngắn, thiếu kid) => không khởi động được, thay vì chạy với khóa yếu.
            app.state.token_signer = TokenSigner(
                settings.webchat_token_secrets,
                active_kid=settings.webchat_token_kid,
                ttl_seconds=settings.webchat_token_ttl_seconds,
            )
        else:
            log.warning("WEBCHAT_TOKEN_SECRETS not set: public webchat API returns 503")
        yield
        await redis.aclose()
        await engine.dispose()

    app = FastAPI(title="assistance-chatbot", lifespan=lifespan)
    http_errors.install(app)
    app.include_router(public_webchat)
    app.include_router(staff_auth)
    app.include_router(staff_inbox)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready(request: Request) -> dict[str, str]:
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {"status": "ready"}

    return app


app = create_app()
