"""Chạy outbox relay: `uv run python -m app.entrypoints.worker`.

Bước 1 chạy đúng MỘT instance (xem docs/design/01-webchat-inbound-pipeline.md §8.4).
"""

import asyncio
import contextlib
import signal
from datetime import timedelta
from functools import partial

import httpx
import redis.asyncio as aioredis
from prometheus_client import start_http_server

from app.config.settings import get_settings
from app.modules.agent_runtime.infrastructure.config import debounce_window
from app.modules.agent_runtime.infrastructure.run_requests import enqueue_from_outbox
from app.modules.knowledge.infrastructure.catalog_sync import run_catalog_scheduler
from app.modules.orders.infrastructure.staff_actions import run_order_maintenance
from app.modules.outbound.infrastructure.deliveries import DeliveryWorker
from app.modules.outbound.infrastructure.messenger_sender import MessengerSender
from app.modules.shadow.infrastructure.comparisons import run_shadow_comparisons
from app.shared.infrastructure.db import create_engine, create_session_factory
from app.shared.infrastructure.logging import configure_logging
from app.workers.outbox_relay import OutboxRelay, RedisPublisher


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    client = aioredis.from_url(settings.redis_url)
    factory = create_session_factory(engine)
    relay = OutboxRelay(
        factory,
        RedisPublisher(client),
        # Tin khách ⇒ hàng đợi agent bền, cùng transaction với outbox (ADR-0008).
        on_published=partial(enqueue_from_outbox, window=debounce_window(settings)),
    )
    start_http_server(settings.worker_metrics_port)  # /metrics nội bộ của relay (lag, ...)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows không hỗ trợ add_signal_handler; Ctrl+C vẫn dừng qua KeyboardInterrupt.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    # Phiếu đặt hàng bỏ dở ⇒ expired; xóa dữ liệu cá nhân quá hạn (thiết kế 03 §5.4).
    # So nháp shadow với câu nhân viên gửi (thiết kế 04): chỉ đọc, không gọi LLM ở S1.
    tasks = [
        relay.run(stop),
        run_order_maintenance(factory, stop),
        run_shadow_comparisons(factory, stop),
    ]
    if settings.google_service_account_json:  # chưa cấu hình service account ⇒ không có gì để sync
        interval = timedelta(minutes=settings.catalog_sync_interval_minutes)
        tasks.append(
            run_catalog_scheduler(factory, settings.google_service_account_json, interval, stop)
        )
    delivery_http: httpx.AsyncClient | None = None
    if settings.channel_credentials_key:
        delivery_http = httpx.AsyncClient(timeout=10.0)
        senders = {
            "messenger": MessengerSender(settings.messenger_graph_base_url, client=delivery_http)
        }
        tasks.append(DeliveryWorker(factory, settings.channel_credentials_key, senders).run(stop))
    try:
        await asyncio.gather(*tasks)
    finally:
        if delivery_http is not None:
            await delivery_http.aclose()
        await client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    configure_logging()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
