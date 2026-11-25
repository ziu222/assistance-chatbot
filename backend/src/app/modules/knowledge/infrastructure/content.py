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
    topics: list[str] | None = None  # None = hệ thống đoán; mảng = chủ shop đã chọn


def _sql(kind: Kind) -> tuple[str, str, str]:
    table, (a, b) = _TABLES[kind]
    return table, a, b


async def list_entries(s: AsyncSession, kind: Kind, tenant_id: UUID) -> list[Entry]:
    table, a, b = _sql(kind)
    rows = await s.execute(
        text(
            f"SELECT id, {a}, {b}, status, created_at, updated_at, topics FROM {table} "
            "WHERE tenant_id = :t ORDER BY created_at"
        ),
        {"t": tenant_id},
    )
    return [Entry(*r) for r in rows]


async def create_entry(
    s: AsyncSession,
    kind: Kind,
    tenant_id: UUID,
    first: str,
    second: str,
    status: str,
    topics: list[str] | None = None,
) -> Entry:
    table, a, b = _sql(kind)
    row = (
        await s.execute(
            text(
                f"INSERT INTO {table} (id, tenant_id, {a}, {b}, status, topics) "
                "VALUES (:id, :t, :x, :y, :st, :tp) "
                f"RETURNING id, {a}, {b}, status, created_at, updated_at, topics"
            ),
            {"id": uuid4(), "t": tenant_id, "x": first, "y": second, "st": status, "tp": topics},
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
    topics: list[str] | None = None,
    set_topics: bool = False,  # True ⇒ ghi `topics` (kể cả None = quay lại để hệ thống đoán)
) -> Entry | None:
    table, a, b = _sql(kind)
    row = (
        await s.execute(
            text(
                f"UPDATE {table} SET {a} = COALESCE(CAST(:x AS text), {a}), "
                f"{b} = COALESCE(CAST(:y AS text), {b}), "
                "status = COALESCE(CAST(:st AS text), status), "
                "topics = CASE WHEN :set_tp THEN CAST(:tp AS text[]) ELSE topics END, "
                "updated_at = now() "
                "WHERE tenant_id = :t AND id = :id "
                f"RETURNING id, {a}, {b}, status, created_at, updated_at, topics"
            ),
            {
                "x": first,
                "y": second,
                "st": status,
                "t": tenant_id,
                "id": entry_id,
                "tp": topics,
                "set_tp": set_topics,
            },
        )
    ).first()
    return Entry(*row) if row else None


async def list_knowledge_gaps(s: AsyncSession, tenant_id: UUID, days: int) -> list[tuple[str, int]]:
    """Đếm tín hiệu `knowledge_gap` theo chủ đề trong `days` ngày gần nhất."""
    rows = await s.execute(
        text(
            "SELECT COALESCE(t.topic, 'other') AS topic, count(*) AS n "
            "FROM learning_signals ls "
            "LEFT JOIN LATERAL jsonb_array_elements_text(ls.payload->'topics') AS t(topic) "
            "ON true "
            "WHERE ls.tenant_id = :t AND ls.kind = 'knowledge_gap' "
            "AND ls.created_at > now() - make_interval(days => :d) "
            "GROUP BY 1 ORDER BY n DESC, topic"
        ),
        {"t": tenant_id, "d": days},
    )
    return [(r.topic, r.n) for r in rows]
