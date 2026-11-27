"""Truy cập và quản lý kết nối kênh bán hàng (`channel_connections`) với credentials mã hóa."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.integration.domain.channels import (
    InvalidChannelInput,
    normalize_page_id,
    normalize_secret,
    verify_token_hash,
)
from app.shared.infrastructure.crypto import InvalidEncryptedPayload, decrypt_json, encrypt_json

log = logging.getLogger(__name__)

_ALLOWED_STATUS = frozenset({"active", "disabled"})


class ChannelAlreadyExists(Exception):
    """Đã tồn tại kết nối active cho cùng provider và `external_account_id`."""


class ChannelNotFound(Exception):
    """Không tìm thấy kết nối thuộc tenant hiện tại."""


@dataclass(frozen=True)
class ChannelSummary:
    id: UUID
    provider: str
    external_account_id: str | None
    status: str
    public_key: str | None
    has_credentials: bool
    created_at: datetime


def _is_unique_external_violation(exc: IntegrityError) -> bool:
    constraint = getattr(exc.orig, "constraint_name", None)
    return constraint == "uq_channel_connection_external" or (
        "uq_channel_connection_external" in str(exc)
    )


def _to_summary(row) -> ChannelSummary:
    return ChannelSummary(
        id=row.id,
        provider=row.provider,
        external_account_id=row.external_account_id,
        status=row.status,
        public_key=row.public_key,
        has_credentials=bool(row.has_credentials),
        created_at=row.created_at,
    )


async def list_channels(s: AsyncSession, tenant_id: UUID) -> list[ChannelSummary]:
    """Liệt kê kết nối kênh của tenant (không bao giờ trả plaintext credentials)."""
    rows = await s.execute(
        text(
            "SELECT id, provider, external_account_id, status, public_key, "
            "(credentials_encrypted IS NOT NULL) AS has_credentials, created_at "
            "FROM channel_connections WHERE tenant_id = :t ORDER BY created_at ASC, id ASC"
        ),
        {"t": tenant_id},
    )
    return [_to_summary(r) for r in rows]


async def create_messenger_channel(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    page_id: str,
    page_access_token: str,
    app_secret: str,
    verify_token: str,
    credentials_key: str,
) -> ChannelSummary:
    """Tạo kết nối Messenger mới với credentials mã hóa Fernet và `verify_token_hash`."""
    clean_page_id = normalize_page_id(page_id)
    clean_pat = normalize_secret(page_access_token, "page_access_token")
    clean_secret = normalize_secret(app_secret, "app_secret")
    clean_verify = normalize_secret(verify_token, "verify_token")

    encrypted = encrypt_json(
        {
            "page_access_token": clean_pat,
            "app_secret": clean_secret,
            "verify_token": clean_verify,
        },
        credentials_key,
    )
    cfg = {"verify_token_hash": verify_token_hash(clean_verify)}
    conn_id = uuid4()
    try:
        row = (
            await s.execute(
                text(
                    "INSERT INTO channel_connections "
                    "(id, tenant_id, provider, external_account_id, status, config, "
                    " credentials_encrypted) "
                    "VALUES (:id, :t, 'messenger', :ext, 'active', CAST(:cfg AS jsonb), :cred) "
                    "RETURNING id, provider, external_account_id, status, public_key, "
                    "(credentials_encrypted IS NOT NULL) AS has_credentials, created_at"
                ),
                {
                    "id": conn_id,
                    "t": tenant_id,
                    "ext": clean_page_id,
                    "cfg": json.dumps(cfg),
                    "cred": encrypted,
                },
            )
        ).one()
    except IntegrityError as exc:
        if _is_unique_external_violation(exc):
            raise ChannelAlreadyExists from exc
        raise

    log.info(
        "messenger channel connected",
        extra={"tenant_id": str(tenant_id), "connection_id": str(conn_id)},
    )
    return _to_summary(row)


async def update_channel(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    connection_id: UUID,
    status: str | None = None,
    page_access_token: str | None = None,
    app_secret: str | None = None,
    verify_token: str | None = None,
    credentials_key: str,
) -> ChannelSummary:
    """Cập nhật trạng thái và/hoặc xoay vòng từng phần credentials cho kết nối kênh."""
    wants_creds = any(v is not None for v in (page_access_token, app_secret, verify_token))
    if status is None and not wants_creds:
        raise InvalidChannelInput("At least one field must be provided")
    if status is not None and status not in _ALLOWED_STATUS:
        raise InvalidChannelInput("status must be 'active' or 'disabled'")

    row = (
        await s.execute(
            text(
                "SELECT id, provider, external_account_id, status, public_key, config, "
                "credentials_encrypted, created_at "
                "FROM channel_connections WHERE tenant_id = :t AND id = :id FOR UPDATE"
            ),
            {"t": tenant_id, "id": connection_id},
        )
    ).first()
    if row is None:
        raise ChannelNotFound

    if wants_creds and row.provider != "messenger":
        raise InvalidChannelInput(
            "Credentials rotation is only supported for messenger connections"
        )

    clean_updates: dict[str, str] = {}
    if page_access_token is not None:
        clean_updates["page_access_token"] = normalize_secret(
            page_access_token, "page_access_token"
        )
    if app_secret is not None:
        clean_updates["app_secret"] = normalize_secret(app_secret, "app_secret")
    if verify_token is not None:
        clean_updates["verify_token"] = normalize_secret(verify_token, "verify_token")

    new_credentials_encrypted = row.credentials_encrypted
    new_config = dict(row.config or {})
    if clean_updates:
        if len(clean_updates) == 3:
            creds: dict[str, str] = {}
        elif row.credentials_encrypted:
            try:
                creds = decrypt_json(row.credentials_encrypted, credentials_key)
            except InvalidEncryptedPayload as exc:
                raise InvalidChannelInput(
                    "Existing credentials cannot be decrypted; provide all credential fields"
                ) from exc
        else:
            creds = {}
        creds.update(clean_updates)
        new_credentials_encrypted = encrypt_json(creds, credentials_key)
        if "verify_token" in clean_updates:
            new_config["verify_token_hash"] = verify_token_hash(clean_updates["verify_token"])

    new_status = status if status is not None else row.status
    try:
        updated = (
            await s.execute(
                text(
                    "UPDATE channel_connections "
                    "SET status = :st, config = CAST(:cfg AS jsonb), "
                    "    credentials_encrypted = :cred "
                    "WHERE tenant_id = :t AND id = :id "
                    "RETURNING id, provider, external_account_id, status, public_key, "
                    "(credentials_encrypted IS NOT NULL) AS has_credentials, created_at"
                ),
                {
                    "st": new_status,
                    "cfg": json.dumps(new_config),
                    "cred": new_credentials_encrypted,
                    "t": tenant_id,
                    "id": connection_id,
                },
            )
        ).one()
    except IntegrityError as exc:
        if _is_unique_external_violation(exc):
            raise ChannelAlreadyExists from exc
        raise

    log.info(
        "channel connection updated",
        extra={
            "tenant_id": str(tenant_id),
            "connection_id": str(connection_id),
            "status": new_status,
            "rotated_fields": sorted(clean_updates),
        },
    )
    return _to_summary(updated)
