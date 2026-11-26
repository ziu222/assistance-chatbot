"""Truy vấn kết nối Facebook Messenger và giải mã credentials theo yêu cầu (ADR-0036)."""

import hashlib
import hmac
import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.infrastructure.crypto import (
    CredentialsKeyNotConfigured,
    InvalidEncryptedPayload,
    decrypt_json,
)

log = logging.getLogger(__name__)

# Chỉ lấy kết nối Messenger đang active thuộc tenant đang active.
_ACTIVE = (
    "SELECT c.id, c.tenant_id, c.external_account_id, c.credentials_encrypted "
    "FROM channel_connections c "
    "JOIN tenants t ON t.id = c.tenant_id "
    "WHERE c.provider = 'messenger' AND c.status = 'active' AND t.status = 'active' "
    "  AND c.credentials_encrypted IS NOT NULL AND "
)


@dataclass(frozen=True)
class MessengerConnection:
    id: UUID
    tenant_id: UUID
    page_id: str
    app_secret: str
    verify_token: str
    page_access_token: str


def verify_token_hash(verify_token: str) -> str:
    """Băm SHA-256 `verify_token` để tra nhanh ở `config->>'verify_token_hash'`."""
    return hashlib.sha256(verify_token.encode("utf-8")).hexdigest()


def _to_connection(row, credentials_key: str) -> MessengerConnection | None:
    if row is None or not row.credentials_encrypted:
        return None
    try:
        creds = decrypt_json(row.credentials_encrypted, credentials_key)
    except (CredentialsKeyNotConfigured, InvalidEncryptedPayload):
        log.warning(
            "failed to decrypt messenger credentials",
            extra={"connection_id": str(row.id)},
        )
        return None
    return MessengerConnection(
        id=row.id,
        tenant_id=row.tenant_id,
        page_id=str(row.external_account_id or ""),
        app_secret=str(creds.get("app_secret") or ""),
        verify_token=str(creds.get("verify_token") or ""),
        page_access_token=str(creds.get("page_access_token") or ""),
    )


async def by_page_id(
    session: AsyncSession, page_id: str, credentials_key: str
) -> MessengerConnection | None:
    """Tìm kết nối Messenger active theo `page_id` (`external_account_id`)."""
    if not page_id:
        return None
    row = (
        await session.execute(
            text(_ACTIVE + "c.external_account_id = :page_id"),
            {"page_id": page_id},
        )
    ).first()
    return _to_connection(row, credentials_key)


async def by_verify_token(
    session: AsyncSession, verify_token: str, credentials_key: str
) -> MessengerConnection | None:
    """Tìm kết nối Messenger active khớp `verify_token` (ưu tiên tra theo `verify_token_hash`)."""
    if not verify_token:
        return None
    token_hash = verify_token_hash(verify_token)
    rows = (
        await session.execute(
            text(_ACTIVE + "c.config->>'verify_token_hash' = :vh"),
            {"vh": token_hash},
        )
    ).all()
    for row in rows:
        conn = _to_connection(row, credentials_key)
        if conn and conn.verify_token and hmac.compare_digest(conn.verify_token, verify_token):
            return conn

    # Dự phòng cho kết nối chưa lưu `verify_token_hash` trong `config`.
    unindexed = (
        await session.execute(
            text(_ACTIVE + "c.config->>'verify_token_hash' IS NULL"),
        )
    ).all()
    for row in unindexed:
        conn = _to_connection(row, credentials_key)
        if conn and conn.verify_token and hmac.compare_digest(conn.verify_token, verify_token):
            return conn
    return None


async def by_connection_id(
    session: AsyncSession, tenant_id: UUID, connection_id: UUID, credentials_key: str
) -> MessengerConnection | None:
    """Tìm kết nối Messenger active theo `(tenant_id, connection_id)` dùng cho chiều gửi."""
    row = (
        await session.execute(
            text(_ACTIVE + "c.tenant_id = :t AND c.id = :id"),
            {"t": tenant_id, "id": connection_id},
        )
    ).first()
    return _to_connection(row, credentials_key)
