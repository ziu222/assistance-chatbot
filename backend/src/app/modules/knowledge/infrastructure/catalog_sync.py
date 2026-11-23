"""Chạy một lần sync từ nguồn ngoài (§13.2) và luôn ghi lại kết quả vào `catalog_sync_runs`."""

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.knowledge.domain.catalog_csv import CatalogCsvError, parse_catalog_table
from app.modules.knowledge.domain.catalog_source import (
    NormalizedItem,
    from_market_listing,
    normalize,
)
from app.modules.knowledge.infrastructure.catalog import SyncResult, record_sync_run, sync_catalog
from app.modules.knowledge.infrastructure.sheets import SheetsError, fetch_values

log = logging.getLogger(__name__)


def market_items(raw_items: list[dict[str, Any]]) -> tuple[list[NormalizedItem], list[str]]:
    items, errors = [], []
    for raw in raw_items:
        try:
            items.append(from_market_listing(raw))
        except ValueError as exc:
            errors.append(str(exc))
    return items, errors


async def get_source_config(s: AsyncSession, tenant_id: UUID, source: str) -> dict | None:
    return (
        await s.execute(
            text("SELECT config FROM catalog_sources WHERE tenant_id = :t AND source = :src"),
            {"t": tenant_id, "src": source},
        )
    ).scalar_one_or_none()


async def save_source_config(s: AsyncSession, tenant_id: UUID, source: str, config: dict) -> None:
    await s.execute(
        text(
            "INSERT INTO catalog_sources (tenant_id, source, config) "
            "VALUES (:t, :src, CAST(:c AS jsonb)) ON CONFLICT (tenant_id, source) "
            "DO UPDATE SET config = EXCLUDED.config, updated_at = now()"
        ),
        {"t": tenant_id, "src": source, "c": json.dumps(config)},
    )


async def recent_runs(s: AsyncSession, tenant_id: UUID, limit: int = 10) -> list[dict]:
    rows = await s.execute(
        text(
            "SELECT source, status, created, updated, deactivated, errors, started_at, finished_at "
            "FROM catalog_sync_runs WHERE tenant_id = :t ORDER BY id DESC LIMIT :n"
        ),
        {"t": tenant_id, "n": limit},
    )
    return [dict(r._mapping) for r in rows]


async def sync_sheets(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    credentials_json: str,
    http: httpx.AsyncClient,
) -> tuple[str, SyncResult]:
    """Đọc sheet đã cấu hình rồi sync đủ (sản phẩm bị xóa khỏi sheet sẽ ẩn). Trả (status, kết quả).

    Bảng có lỗi ⇒ không ghi sản phẩm nào, giống nhập CSV; lần sync vẫn được ghi là `failed`.
    """
    started = datetime.now(UTC)
    async with factory() as s:
        config = await get_source_config(s, tenant_id, "sheets")
    try:
        if config is None:
            raise SheetsError("not_configured")
        values = await fetch_values(
            credentials_json, config["spreadsheet_id"], config["range"], http
        )
        items = [normalize(i, i.sku) for i in parse_catalog_table(values)]
    except (SheetsError, CatalogCsvError) as exc:
        failed = SyncResult(errors=exc.errors if isinstance(exc, CatalogCsvError) else [str(exc)])
        async with factory() as s, s.begin():
            await record_sync_run(s, tenant_id, "sheets", started, failed, failed=True)
        return "failed", failed
    async with factory() as s, s.begin():
        result = await sync_catalog(s, tenant_id, "sheets", items, full=True)
        await record_sync_run(s, tenant_id, "sheets", started, result)
    return ("partial" if result.errors else "ok"), result


async def sync_due_sources(
    factory: async_sessionmaker[AsyncSession],
    credentials_json: str,
    http: httpx.AsyncClient,
    interval: timedelta,
) -> int:
    """Sync mọi nguồn Sheets chưa có lần sync nào (kể cả thất bại) trong `interval`.

    Lần thất bại cũng tính: nguồn hỏng được thử lại sau một chu kỳ chứ không bị gọi liên tục.
    Khóa dòng `catalog_sources` bằng SKIP LOCKED nên chạy nhiều worker không sync trùng.
    """
    done = 0
    while True:
        async with factory() as s, s.begin():
            tenant_id = (
                await s.execute(
                    text(
                        "SELECT cs.tenant_id FROM catalog_sources cs "
                        "WHERE cs.source = 'sheets' AND NOT EXISTS ("
                        "  SELECT 1 FROM catalog_sync_runs r WHERE r.tenant_id = cs.tenant_id "
                        "  AND r.source = 'sheets' "
                        "  AND r.finished_at > now() - make_interval(secs => :secs)) "
                        "ORDER BY cs.updated_at LIMIT 1 FOR UPDATE OF cs SKIP LOCKED"
                    ),
                    {"secs": interval.total_seconds()},
                )
            ).scalar_one_or_none()
            if tenant_id is None:
                return done
            # Giữ khóa trong lúc sync (transaction khác ghi sản phẩm + lần sync).
            await sync_sheets(factory, tenant_id, credentials_json, http)
            done += 1


async def run_catalog_scheduler(
    factory: async_sessionmaker[AsyncSession],
    credentials_json: str,
    interval: timedelta,
    stop: asyncio.Event,
    tick_s: float = 60.0,
) -> None:
    async with httpx.AsyncClient() as http:
        while not stop.is_set():
            try:
                await sync_due_sources(factory, credentials_json, http, interval)
            except Exception:
                log.exception("catalog scheduler tick failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=tick_s)
