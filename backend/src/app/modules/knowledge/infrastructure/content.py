"""FAQ đã duyệt và tài liệu nhỏ: CRUD theo tenant. Xóa = lưu trữ (nháp cũ còn trích nguồn tới)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Kind = Literal["faq", "doc"]

# Tên bảng/cột cố định theo loại; không bao giờ lấy từ input.
_TABLES = {
    "faq": ("faq_entries", ("question", "answer")),
    "doc": ("knowledge_docs", ("title", "body")),
}


@dataclass(frozen=True)
class Entry:
    id: UUID
    first: str  # question | title
    second: str  # answer | body
    status: str
    created_at: datetime
    updated_at: datetime


def _sql(kind: Kind) -> tuple[str, str, str]:
    table, (a, b) = _TABLES[kind]
    return table, a, b


async def list_entries(s: AsyncSession, kind: Kind, tenant_id: UUID) -> list[Entry]:
    table, a, b = _sql(kind)
    rows = await s.execute(
        text(
            f"SELECT id, {a}, {b}, status, created_at, updated_at FROM {table} "
            "WHERE tenant_id = :t ORDER BY created_at"
        ),
        {"t": tenant_id},
    )
    return [Entry(*r) for r in rows]


async def create_entry(
    s: AsyncSession, kind: Kind, tenant_id: UUID, first: str, second: str, status: str
) -> Entry:
    table, a, b = _sql(kind)
    row = (
        await s.execute(
            text(
                f"INSERT INTO {table} (id, tenant_id, {a}, {b}, status) "
                "VALUES (:id, :t, :x, :y, :st) "
                f"RETURNING id, {a}, {b}, status, created_at, updated_at"
            ),
            {"id": uuid4(), "t": tenant_id, "x": first, "y": second, "st": status},
        )
    ).one()
    return Entry(*row)


async def update_entry(
    s: AsyncSession,
    kind: Kind,
    tenant_id: UUID,
    entry_id: UUID,
    *,
    first: str | None,
    second: str | None,
    status: str | None,
) -> Entry | None:
    table, a, b = _sql(kind)
    row = (
        await s.execute(
            text(
                f"UPDATE {table} SET {a} = COALESCE(CAST(:x AS text), {a}), "
                f"{b} = COALESCE(CAST(:y AS text), {b}), "
                "status = COALESCE(CAST(:st AS text), status), updated_at = now() "
                "WHERE tenant_id = :t AND id = :id "
                f"RETURNING id, {a}, {b}, status, created_at, updated_at"
            ),
            {"x": first, "y": second, "st": status, "t": tenant_id, "id": entry_id},
        )
    ).first()
    return Entry(*row) if row else None
