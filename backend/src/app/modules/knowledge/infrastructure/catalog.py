"""Catalog có cấu trúc (§11.0): giá/tồn luôn đọc từ bảng, không từ văn bản embed."""

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge.domain.catalog_csv import ParsedItem
from app.modules.knowledge.domain.text import fold


@dataclass(frozen=True)
class CatalogItem:
    id: UUID
    sku: str
    name: str
    description: str | None
    price_vnd: int
    variants: list[dict]


_COLUMNS = "id, sku, name, description, price_vnd, variants"

_UPSERT = text(
    """
    INSERT INTO catalog_items
      (id, tenant_id, sku, name, description, price_vnd, variants, search_text)
    VALUES (:id, :t, :sku, :name, :description, :price, CAST(:variants AS jsonb), :search)
    ON CONFLICT (tenant_id, sku) DO UPDATE SET
      name = EXCLUDED.name, description = EXCLUDED.description, price_vnd = EXCLUDED.price_vnd,
      variants = EXCLUDED.variants, search_text = EXCLUDED.search_text,
      active = true, synced_at = now()
    RETURNING (xmax = 0) AS inserted
    """
)


async def import_catalog(
    s: AsyncSession, tenant_id: UUID, items: list[ParsedItem]
) -> tuple[int, int]:
    """Upsert theo SKU trong transaction của caller (cả file hoặc không gì). Trả (tạo, cập nhật)."""
    created = 0
    for item in items:
        inserted = (
            await s.execute(
                _UPSERT,
                {
                    "id": uuid4(),
                    "t": tenant_id,
                    "sku": item.sku,
                    "name": item.name,
                    "description": item.description,
                    "price": item.price_vnd,
                    "variants": json.dumps(item.variants, ensure_ascii=False),
                    "search": fold(f"{item.name} {item.description or ''}"),
                },
            )
        ).scalar_one()
        created += int(inserted)
    return created, len(items) - created


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
