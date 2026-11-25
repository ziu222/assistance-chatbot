import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.config.settings import Settings, get_settings
from app.modules.agent_management.presentation.http.agents import router as agents_api
from app.modules.agent_runtime.presentation.http.agent_runs import router as agent_runs_api
from app.modules.authoring.presentation.http.writing import router as writing_api
from app.modules.conversation_policy.presentation.http.policy import router as policy_api
from app.modules.identity.presentation.http.auth import router as staff_auth
from app.modules.inbox.presentation.http.events import router as staff_events
from app.modules.inbox.presentation.http.staff_inbox import router as staff_inbox
from app.modules.integration.infrastructure.webchat.token import TokenSigner
from app.modules.integration.presentation.http.public_webchat import router as public_webchat
from app.modules.knowledge.presentation.http.knowledge import router as knowledge_api
from app.modules.learning.presentation.http.candidates import router as learning_candidates_api
from app.modules.learning.presentation.http.drafts import router as learning_drafts_api
from app.modules.orders.presentation.http.order_flow import router as order_flow_api
from app.modules.orders.presentation.http.order_requests import router as order_requests_api
from app.modules.outbound.presentation.http.autopilot import router as autopilot_api
from app.modules.shadow.presentation.http.shadow import router as shadow_api
from app.shared.infrastructure.db import create_engine, create_session_factory
from app.shared.infrastructure.metrics import OUTBOX_OLDEST_AGE, OUTBOX_UNPUBLISHED
from app.shared.infrastructure.rate_limit import RateLimiter
from app.shared.infrastructure.sse_hub import SignalHub
from app.shared.presentation import http_errors
from app.shared.presentation.widget_static import WidgetStatic
from app.workers.outbox_relay import outbox_backlog

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
        app.state.redis = redis
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.rate_limiter = RateLimiter(redis)
        app.state.signal_hub = SignalHub(settings.redis_url)
        app.state.signal_hub.start()  # Redis chết lúc khởi động: hub tự thử lại ở nền
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
        await app.state.signal_hub.stop()
        await redis.aclose()
        await engine.dispose()

    app = FastAPI(title="assistance-chatbot", lifespan=lifespan)
    http_errors.install(app)
    app.include_router(public_webchat)
    app.include_router(staff_auth)
    app.include_router(staff_inbox)
    app.include_router(staff_events)
    app.include_router(agents_api)
    app.include_router(agent_runs_api)
    app.include_router(knowledge_api)
    app.include_router(policy_api)
    app.include_router(writing_api)
    app.include_router(order_flow_api)
    app.include_router(order_requests_api)
    app.include_router(autopilot_api)
    app.include_router(shadow_api)
    app.include_router(learning_drafts_api)
    app.include_router(learning_candidates_api)
    # check_dir=False: CI/backend-only chạy được khi chưa build widget (khi đó /widget/* trả 404).
    app.mount("/widget", WidgetStatic(directory=settings.widget_dist_dir, check_dir=False))

    outbox_metrics_ttl_s = 10.0
    outbox_metrics_refreshed_at: float | None = None

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        # ponytail: endpoint nội bộ; reverse proxy KHÔNG được mở /metrics ra Internet (runbook).
        # Cache ngắn hạn để Prometheus scrape dày hoặc nhiều replica không dội query lên Postgres.
        nonlocal outbox_metrics_refreshed_at
        now = time.monotonic()
        if (
            outbox_metrics_refreshed_at is None
            or now - outbox_metrics_refreshed_at >= outbox_metrics_ttl_s
        ):
            try:
                async with request.app.state.session_factory() as s:
                    count, age = await outbox_backlog(s)
                OUTBOX_UNPUBLISHED.set(count)
                OUTBOX_OLDEST_AGE.set(age)
                outbox_metrics_refreshed_at = now
            except Exception:
                log.warning("metrics: outbox backlog unavailable")
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

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
        # Redis chỉ báo, không làm fail: mất Redis vẫn phải nhận/lưu tin (thiết kế §16.6);
        # nếu fail thì load balancer gỡ mọi instance và chat sập theo Redis.
        try:
            await request.app.state.redis.ping()
            redis_state = "ok"
        except Exception:
            redis_state = "down"
        return {"status": "ready", "redis": redis_state}

    return app


app = create_app()
