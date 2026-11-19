"""Session nhân viên lưu DB: cookie mang giá trị ngẫu nhiên 256-bit, DB chỉ giữ sha256."""

import hashlib
import secrets
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.domain.authorization import StaffActor
from app.modules.identity.infrastructure.passwords import verify_password

# Mọi thứ phải còn hiệu lực: user, membership, tenant.
_ACTIVE_MEMBERSHIPS = (
    "SELECT u.id AS user_id, u.email, u.display_name, m.tenant_id, m.role, "
    "t.name AS tenant_name, t.slug AS tenant_slug "
    "FROM users u JOIN tenant_memberships m ON m.user_id = u.id "
    "JOIN tenants t ON t.id = m.tenant_id "
    "WHERE u.status = 'active' AND m.status = 'active' AND t.status = 'active' "
)


def _digest(raw: str) -> bytes:
    return hashlib.sha256(raw.encode()).digest()


def _actor(row) -> StaffActor:
    return StaffActor(
        row.user_id,
        row.tenant_id,
        row.role,
        str(row.email),
        row.display_name,
        row.tenant_name,
        row.tenant_slug,
    )


class TenantRequired(Exception):
    """User thuộc nhiều tenant: phải chọn tenant khi đăng nhập."""


async def authenticate(
    s: AsyncSession, email: str, password: str, tenant_slug: str | None
) -> StaffActor | None:
    user = (
        await s.execute(
            text("SELECT id, password_hash FROM users WHERE email = :e AND status = 'active'"),
            {"e": email},
        )
    ).first()
    # Luôn verify (dùng hash giả khi không có user) để không lộ email tồn tại qua thời gian.
    if not verify_password(user.password_hash if user else None, password):
        return None
    rows = (
        await s.execute(text(_ACTIVE_MEMBERSHIPS + "AND u.id = :u ORDER BY t.slug"), {"u": user.id})
    ).all()
    if tenant_slug is not None:
        rows = [r for r in rows if r.tenant_slug == tenant_slug]
    if not rows:
        return None
    if len(rows) > 1:
        raise TenantRequired
    return _actor(rows[0])


async def create_session(s: AsyncSession, actor: StaffActor, ttl: timedelta) -> str:
    raw = secrets.token_urlsafe(32)  # 256 bit
    await s.execute(
        text(
            "INSERT INTO staff_sessions (id, tenant_id, user_id, token_hash, expires_at) "
            "VALUES (:id, :t, :u, :h, now() + :ttl)"
        ),
        {"id": uuid4(), "t": actor.tenant_id, "u": actor.user_id, "h": _digest(raw), "ttl": ttl},
    )
    return raw


async def actor_for_session(s: AsyncSession, raw: str) -> StaffActor | None:
    row = (
        await s.execute(
            text(
                _ACTIVE_MEMBERSHIPS + "AND (m.tenant_id, u.id) = ("
                "SELECT tenant_id, user_id FROM staff_sessions "
                "WHERE token_hash = :h AND expires_at > now())"
            ),
            {"h": _digest(raw)},
        )
    ).first()
    return _actor(row) if row else None


async def delete_session(s: AsyncSession, raw: str) -> None:
    await s.execute(text("DELETE FROM staff_sessions WHERE token_hash = :h"), {"h": _digest(raw)})
