"""Chạy một lần sync từ nguồn ngoài (§13.2) và luôn ghi lại kết quả vào `catalog_sync_runs`."""

import json
from datetime import UTC, datetime
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
