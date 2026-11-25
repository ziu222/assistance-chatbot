"""Bản phát hành tri thức có phiên bản và con trỏ hiệu lực (thiết kế 05 L2, ADR-0038).

Mỗi bản phát hành lưu ảnh chụp bất biến toàn bộ FAQ/tài liệu đã duyệt; `ContextBuilder` đọc từ
ảnh chụp của bản mà `tenant_knowledge` trỏ tới. Quay lại = đổi con trỏ.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge.domain.releases import (
    KnowledgeSnapshot,
    ReleaseReason,
    SnapshotItem,
    build_snapshot,
)
from app.modules.knowledge.infrastructure.content import list_entries
from app.shared.infrastructure.signals import record_signal


@dataclass(frozen=True)
class KnowledgeRelease:
    id: UUID
    tenant_id: UUID
    number: int
    snapshot: KnowledgeSnapshot
    reason: ReleaseReason
    created_by: UUID | None
    eval_run_id: UUID | None
    created_at: datetime
    is_current: bool


async def snapshot_from_tables(s: AsyncSession, tenant_id: UUID) -> KnowledgeSnapshot:
    """Dựng ảnh chụp xác định từ các mục FAQ/tài liệu đang ở trạng thái `approved`."""
    faqs = [
        SnapshotItem(
            id=str(e.id),
            first=e.first,
            second=e.second,
            topics=tuple(e.topics) if e.topics is not None else None,
        )
        for e in await list_entries(s, "faq", tenant_id)
        if e.status == "approved"
    ]
    docs = [
        SnapshotItem(
            id=str(e.id),
            first=e.first,
            second=e.second,
            topics=tuple(e.topics) if e.topics is not None else None,
        )
        for e in await list_entries(s, "doc", tenant_id)
        if e.status == "approved"
    ]
    return build_snapshot(faqs, docs)


async def current_release_id(s: AsyncSession, tenant_id: UUID) -> UUID | None:
    return (
        await s.execute(
            text("SELECT current_release_id FROM tenant_knowledge WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()


async def active_snapshot(s: AsyncSession, tenant_id: UUID) -> KnowledgeSnapshot:
    """Ảnh chụp của bản phát hành đang hiệu lực; nếu tenant chưa có bản nào thì đọc từ bảng."""
    row = (
        await s.execute(
            text(
                "SELECT r.snapshot FROM tenant_knowledge tk "
                "JOIN knowledge_releases r "
                "  ON r.tenant_id = tk.tenant_id AND r.id = tk.current_release_id "
                "WHERE tk.tenant_id = :t"
            ),
            {"t": tenant_id},
        )
    ).first()
    if row is not None:
        return KnowledgeSnapshot.from_dict(row.snapshot)
    return await snapshot_from_tables(s, tenant_id)


async def publish_snapshot(
    s: AsyncSession,
    tenant_id: UUID,
    snapshot: KnowledgeSnapshot,
    *,
    reason: ReleaseReason,
    created_by: UUID | None,
    eval_run_id: UUID | None = None,
) -> KnowledgeRelease:
    """Tạo bản phát hành mới và chuyển con trỏ `tenant_knowledge` tới bản đó."""
    await s.execute(text("SELECT 1 FROM tenants WHERE id = :t FOR UPDATE"), {"t": tenant_id})
    next_number = (
        await s.execute(
            text(
                "SELECT COALESCE(MAX(number), 0) + 1 FROM knowledge_releases WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    release_id = uuid4()
    row = (
        await s.execute(
            text(
                "INSERT INTO knowledge_releases "
                "(id, tenant_id, number, snapshot, reason, created_by, eval_run_id) "
                "VALUES (:id, :t, :n, CAST(:snap AS jsonb), :r, :by, :ev) "
                "RETURNING id, tenant_id, number, snapshot, reason, created_by, eval_run_id, "
                "created_at"
            ),
            {
                "id": release_id,
                "t": tenant_id,
                "n": next_number,
                "snap": json.dumps(snapshot.as_dict(), ensure_ascii=False),
                "r": reason,
                "by": created_by,
                "ev": eval_run_id,
            },
        )
    ).one()
    await s.execute(
        text(
            "INSERT INTO tenant_knowledge (tenant_id, current_release_id) "
            "VALUES (:t, :id) "
            "ON CONFLICT (tenant_id) DO UPDATE "
            "SET current_release_id = EXCLUDED.current_release_id, updated_at = now()"
        ),
        {"t": tenant_id, "id": release_id},
    )
    await record_signal(
        s,
        tenant_id,
        None,
        "release_published",
        {"release_id": str(release_id), "number": next_number, "reason": reason},
    )
    return KnowledgeRelease(
        id=row.id,
        tenant_id=row.tenant_id,
        number=row.number,
        snapshot=KnowledgeSnapshot.from_dict(row.snapshot),
        reason=row.reason,
        created_by=row.created_by,
        eval_run_id=row.eval_run_id,
        created_at=row.created_at,
        is_current=True,
    )


async def publish_from_tables(
    s: AsyncSession,
    tenant_id: UUID,
    *,
    reason: ReleaseReason = "manual_edit",
    created_by: UUID | None,
) -> KnowledgeRelease:
    snapshot = await snapshot_from_tables(s, tenant_id)
    return await publish_snapshot(s, tenant_id, snapshot, reason=reason, created_by=created_by)


async def list_releases(
    s: AsyncSession, tenant_id: UUID, limit: int = 50
) -> list[KnowledgeRelease]:
    rows = await s.execute(
        text(
            "SELECT r.id, r.tenant_id, r.number, r.snapshot, r.reason, r.created_by, "
            "       r.eval_run_id, r.created_at, "
            "       (tk.current_release_id = r.id) AS is_current "
            "FROM knowledge_releases r "
            "LEFT JOIN tenant_knowledge tk ON tk.tenant_id = r.tenant_id "
            "WHERE r.tenant_id = :t "
            "ORDER BY r.number DESC LIMIT :lim"
        ),
        {"t": tenant_id, "lim": limit},
    )
    return [
        KnowledgeRelease(
            id=r.id,
            tenant_id=r.tenant_id,
            number=r.number,
            snapshot=KnowledgeSnapshot.from_dict(r.snapshot),
            reason=r.reason,
            created_by=r.created_by,
            eval_run_id=r.eval_run_id,
            created_at=r.created_at,
            is_current=bool(r.is_current),
        )
        for r in rows
    ]


async def restore_release(
    s: AsyncSession, tenant_id: UUID, number: int, *, restored_by: UUID
) -> KnowledgeRelease | None:
    """Quay lại bản phát hành `number` bằng cách đổi con trỏ `tenant_knowledge`."""
    row = (
        await s.execute(
            text(
                "SELECT id, tenant_id, number, snapshot, reason, created_by, eval_run_id, "
                "       created_at "
                "FROM knowledge_releases WHERE tenant_id = :t AND number = :n"
            ),
            {"t": tenant_id, "n": number},
        )
    ).first()
    if row is None:
        return None
    await s.execute(text("SELECT 1 FROM tenants WHERE id = :t FOR UPDATE"), {"t": tenant_id})
    snapshot = KnowledgeSnapshot.from_dict(row.snapshot)
    await _sync_tables_to(s, tenant_id, snapshot)
    await s.execute(
        text(
            "INSERT INTO tenant_knowledge (tenant_id, current_release_id) "
            "VALUES (:t, :id) "
            "ON CONFLICT (tenant_id) DO UPDATE "
            "SET current_release_id = EXCLUDED.current_release_id, updated_at = now()"
        ),
        {"t": tenant_id, "id": row.id},
    )
    await record_signal(
        s,
        tenant_id,
        None,
        "release_published",
        {
            "release_id": str(row.id),
            "number": row.number,
            "reason": "restore",
            "restored_by": str(restored_by),
        },
    )
    return KnowledgeRelease(
        id=row.id,
        tenant_id=row.tenant_id,
        number=row.number,
        snapshot=KnowledgeSnapshot.from_dict(row.snapshot),
        reason=row.reason,
        created_by=row.created_by,
        eval_run_id=row.eval_run_id,
        created_at=row.created_at,
        is_current=True,
    )


_TABLES = (
    ("faqs", "faq_entries", "question", "answer"),
    ("docs", "knowledge_docs", "title", "body"),
)


async def _sync_tables_to(s: AsyncSession, tenant_id: UUID, snapshot: KnowledgeSnapshot) -> None:
    """Đưa bảng soạn thảo về đúng ảnh chụp. Lần sửa tay kế tiếp dựng lại từ bảng, nên nếu chỉ đổi
    con trỏ thì nội dung vừa quay lại sẽ bị lần sửa đó lặng lẽ ghi đè."""
    for attr, table, first, second in _TABLES:
        items = getattr(snapshot, attr)
        await s.execute(
            text(
                f"UPDATE {table} SET status = 'archived', updated_at = now() "  # noqa: S608 - tên bảng cố định
                "WHERE tenant_id = :t AND status = 'approved' AND NOT (id = ANY(:ids))"
            ),
            {"t": tenant_id, "ids": [UUID(e.id) for e in items]},
        )
        for e in items:
            await s.execute(
                text(
                    f"INSERT INTO {table} AS x (id, tenant_id, {first}, {second}, status, topics) "  # noqa: S608
                    "VALUES (:id, :t, :a, :b, 'approved', :tp) ON CONFLICT (id) DO UPDATE SET "
                    f"{first} = EXCLUDED.{first}, {second} = EXCLUDED.{second}, "
                    "status = 'approved', topics = EXCLUDED.topics, updated_at = now() "
                    "WHERE x.tenant_id = EXCLUDED.tenant_id"
                ),
                {
                    "id": UUID(e.id),
                    "t": tenant_id,
                    "a": e.first,
                    "b": e.second,
                    "tp": list(e.topics) if e.topics is not None else None,
                },
            )
