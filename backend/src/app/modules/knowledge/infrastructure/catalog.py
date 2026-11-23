"""Catalog có cấu trúc (§11.0): giá/tồn luôn đọc từ bảng, không từ văn bản embed."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge.domain.catalog_csv import CatalogCsvError, ParsedItem
from app.modules.knowledge.domain.catalog_source import NormalizedItem, normalize
from app.modules.knowledge.domain.text import fold


@dataclass(frozen=True)
class CatalogItem:
    id: UUID
    sku: str
    name: str
    description: str | None
    price_vnd: int
    variants: list[dict]
    source: str = "csv"
    synced_at: datetime | None = None
    url: str | None = None


_COLUMNS = "id, sku, name, description, price_vnd, variants, source, synced_at, url"

_UPSERT = text(
    """
    INSERT INTO catalog_items (id, tenant_id, source, external_id, sku, name, description,
                               price_vnd, variants, search_text, url)
    VALUES (:id, :t, :src, :ext, :sku, :name, :description, :price, CAST(:variants AS jsonb),
            :search, :url)
    ON CONFLICT (tenant_id, source, external_id) DO UPDATE SET
      sku = EXCLUDED.sku, name = EXCLUDED.name, description = EXCLUDED.description,
      price_vnd = EXCLUDED.price_vnd, variants = EXCLUDED.variants,
      search_text = EXCLUDED.search_text, url = EXCLUDED.url, active = true, synced_at = now()
    RETURNING (xmax = 0) AS inserted
    """
)


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    errors: list[str] = field(default_factory=list)


async def sync_catalog(
    s: AsyncSession,
    tenant_id: UUID,
    source: str,
    items: list[NormalizedItem],
    *,
    full: bool,
    errors: list[str] | None = None,
) -> SyncResult:
    """Upsert theo (nguồn, external_id) trong transaction của caller; chạy lại không tạo trùng.

    Một nguồn sở hữu mỗi sản phẩm: SKU đã thuộc nguồn khác thì bỏ qua và báo lỗi. `full` = nguồn
    vừa trả đủ danh sách ⇒ sản phẩm của nguồn này không còn trong đó bị ẩn (không xóa: nháp cũ
    vẫn trích được). Có lỗi bất kỳ thì không ẩn gì, vì dòng lỗi không có nghĩa là đã bị gỡ.
    """
    result = SyncResult(errors=list(errors or []))
    owned = dict(
        (
            await s.execute(
                text(
                    "SELECT sku, source FROM catalog_items "
                    "WHERE tenant_id = :t AND source <> :src AND sku = ANY(:skus)"
                ),
                {"t": tenant_id, "src": source, "skus": [i.sku for i in items]},
            )
        ).all()
    )
    kept: list[str] = []
    for item in items:
        if item.sku in owned:
            result.errors.append(f"sku {item.sku} belongs to source {owned[item.sku]}")
            continue
        inserted = (
            await s.execute(
                _UPSERT,
                {
                    "id": uuid4(),
                    "t": tenant_id,
                    "src": source,
                    "ext": item.external_id,
                    "sku": item.sku,
                    "name": item.name,
                    "description": item.description,
                    "price": item.price_vnd,
                    "variants": json.dumps(item.variants, ensure_ascii=False),
                    "search": item.search_text,
                    "url": item.url,
                },
            )
        ).scalar_one()
        kept.append(item.external_id)
        if inserted:
            result.created += 1
        else:
            result.updated += 1
    if full and not result.errors:
        result.deactivated = (
            await s.execute(
                text(
                    "UPDATE catalog_items SET active = false WHERE tenant_id = :t "
                    "AND source = :src AND active AND NOT (external_id = ANY(:kept))"
                ),
                {"t": tenant_id, "src": source, "kept": kept},
            )
        ).rowcount
    return result


async def import_catalog(
    s: AsyncSession, tenant_id: UUID, items: list[ParsedItem]
) -> tuple[int, int]:
    """CSV: cả file hoặc không gì (lỗi ⇒ CatalogCsvError, caller rollback). Trả (tạo, cập nhật).

    ponytail: file CSV chỉ cập nhật, không ẩn sản phẩm vắng mặt (chủ shop hay tải file một phần).
    """
    result = await sync_catalog(
        s, tenant_id, "csv", [normalize(i, i.sku) for i in items], full=False
    )
    if result.errors:
        raise CatalogCsvError(result.errors)
    return result.created, result.updated


async def record_sync_run(
    s: AsyncSession,
    tenant_id: UUID,
    source: str,
    started_at: datetime,
    result: SyncResult,
    *,
    failed: bool = False,
) -> None:
    status = "failed" if failed else "partial" if result.errors else "ok"
    await s.execute(
        text(
            "INSERT INTO catalog_sync_runs (tenant_id, source, status, created, updated, "
            "deactivated, errors, started_at) "
            "VALUES (:t, :src, :st, :c, :u, :d, CAST(:e AS jsonb), :at)"
        ),
        {
            "t": tenant_id,
            "src": source,
            "st": status,
            "c": result.created,
            "u": result.updated,
            "d": result.deactivated,
            "e": json.dumps(result.errors[:20], ensure_ascii=False),
            "at": started_at,
        },
    )


async def search_catalog(
    s: AsyncSession, tenant_id: UUID, query: str, limit: int
) -> list[CatalogItem]:
    """Tìm không dấu bằng trigram: `q <% search_text` khớp q với một đoạn bất kỳ của tên/mô tả.

    Khách hay hỏi thẳng theo mã ("Mã TE-076 còn không"): câu hỏi chứa SKU thì sản phẩm đó luôn
    khớp và đứng đầu. So trong SQL nên không cần dựng lại `search_text` của dữ liệu cũ."""
    q = fold(query)
    if not q:
        return []
    rows = await s.execute(
        text(
            f"SELECT {_COLUMNS} FROM catalog_items "
            "WHERE tenant_id = :t AND active "
            "AND (:q <% search_text OR strpos(:q, lower(sku)) > 0) "
            "ORDER BY (strpos(:q, lower(sku)) > 0) DESC, word_similarity(:q, search_text) DESC, "
            "name LIMIT :limit"
        ),
        {"t": tenant_id, "q": q, "limit": limit},
    )
    return [CatalogItem(*r) for r in rows]


async def get_catalog_item(s: AsyncSession, tenant_id: UUID, item_id: UUID) -> CatalogItem | None:
    row = (
        await s.execute(
            text(
                f"SELECT {_COLUMNS} FROM catalog_items WHERE tenant_id = :t AND id = :id AND active"
            ),
            {"t": tenant_id, "id": item_id},
        )
    ).first()
    return CatalogItem(*row) if row else None
