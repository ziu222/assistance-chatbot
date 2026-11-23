"""Truy cập dữ liệu trợ lý viết: SQL thẳng, mọi câu lọc tenant_id."""

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.authoring.domain.writing import BrandProfile

MAX_PENDING_PER_TENANT = 3  # chống bấm "Gợi ý" liên tục làm tốn tiền


@dataclass(frozen=True)
class WritingRequest:
    id: UUID
    tenant_id: UUID
    kind: str
    level: str
    text: str
    status: str
    suggestions: list | None
    error_code: str | None
    created_at: datetime


_COLS = "id, tenant_id, kind, level, text, status, suggestions, error_code, created_at"


async def create_request(
    s: AsyncSession, tenant_id: UUID, *, kind: str, level: str, text_: str, created_by: UUID
) -> UUID | None:
    """None nếu tenant đã có quá nhiều yêu cầu đang chờ."""
    pending = (
        await s.execute(
            text(
                "SELECT count(*) FROM writing_requests "
                "WHERE tenant_id = :t AND status IN ('pending','running')"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    if pending >= MAX_PENDING_PER_TENANT:
        return None
    rid = uuid4()
    await s.execute(
        text(
            "INSERT INTO writing_requests (id, tenant_id, kind, level, text, created_by) "
            "VALUES (:id, :t, :k, :l, :x, :by)"
        ),
        {"id": rid, "t": tenant_id, "k": kind, "l": level, "x": text_, "by": created_by},
    )
    return rid


async def get_request(s: AsyncSession, tenant_id: UUID, rid: UUID) -> WritingRequest | None:
    row = (
        await s.execute(
            text(f"SELECT {_COLS} FROM writing_requests WHERE tenant_id = :t AND id = :id"),
            {"t": tenant_id, "id": rid},
        )
    ).first()
    return WritingRequest(*row) if row else None


async def claim_next(s: AsyncSession, lease_seconds: int = 60) -> WritingRequest | None:
    """Lấy một yêu cầu chờ (hoặc yêu cầu của worker đã chết: lease hết hạn). SKIP LOCKED ⇒
    nhiều worker không tranh nhau."""
    row = (
        await s.execute(
            text(
                f"UPDATE writing_requests SET status = 'running', "
                f"lease_until = now() + make_interval(secs => :lease) "
                "WHERE id = (SELECT id FROM writing_requests "
                "  WHERE status = 'pending' OR (status = 'running' AND lease_until < now()) "
                "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
                f"RETURNING {_COLS}"
            ),
            {"lease": lease_seconds},
        )
    ).first()
    return WritingRequest(*row) if row else None


async def finish(
    s: AsyncSession,
    rid: UUID,
    *,
    suggestions: list | None = None,
    error_code: str | None = None,
) -> None:
    await s.execute(
        text(
            "UPDATE writing_requests SET status = :st, suggestions = CAST(:sg AS jsonb), "
            "error_code = :e, finished_at = now(), lease_until = NULL WHERE id = :id"
        ),
        {
            "st": "failed" if error_code else "done",
            "sg": json.dumps(suggestions, ensure_ascii=False) if suggestions is not None else None,
            "e": error_code,
            "id": rid,
        },
    )


async def current_profile(s: AsyncSession, tenant_id: UUID) -> tuple[int, BrandProfile] | None:
    row = (
        await s.execute(
            text(
                "SELECT number, config FROM brand_profiles WHERE tenant_id = :t "
                "ORDER BY number DESC LIMIT 1"
            ),
            {"t": tenant_id},
        )
    ).first()
    return (row[0], BrandProfile.model_validate(row[1])) if row else None


async def save_profile(
    s: AsyncSession, tenant_id: UUID, profile: BrandProfile, created_by: UUID
) -> int:
    """Lưu = bản mới (bất biến). Hai người lưu cùng lúc ⇒ vi phạm UNIQUE ⇒ route trả 409."""
    row = (
        await s.execute(
            text(
                "INSERT INTO brand_profiles (id, tenant_id, number, config, created_by) "
                "SELECT :id, :t, COALESCE(MAX(number), 0) + 1, CAST(:cfg AS jsonb), :by "
                "FROM brand_profiles WHERE tenant_id = :t RETURNING number"
            ),
            {"id": uuid4(), "t": tenant_id, "cfg": profile.model_dump_json(), "by": created_by},
        )
    ).one()
    return row[0]
