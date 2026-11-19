from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.integration.domain.origin import valid_origins

PUBLIC_CONFIG_KEYS = ("greeting", "theme", "privacy_url")

# Chỉ connection webchat đang active của tenant đang active.
_ACTIVE = (
    "SELECT c.id, c.tenant_id, c.config FROM channel_connections c "
    "JOIN tenants t ON t.id = c.tenant_id "
    "WHERE c.provider = 'webchat' AND c.status = 'active' AND t.status = 'active' AND "
)


@dataclass(frozen=True)
class WebchatConnection:
    id: UUID
    tenant_id: UUID
    allowed_origins: list[str]
    public_config: dict[str, Any]


def _row(row) -> WebchatConnection | None:
    if row is None:
        return None
    config = row.config or {}
    return WebchatConnection(
        row.id,
        row.tenant_id,
        valid_origins(config.get("allowed_origins")),
        {k: config[k] for k in PUBLIC_CONFIG_KEYS if k in config},
    )


async def by_public_key(session: AsyncSession, public_key: str) -> WebchatConnection | None:
    row = (await session.execute(text(_ACTIVE + "c.public_key = :k"), {"k": public_key})).first()
    return _row(row)


async def by_id(
    session: AsyncSession, tenant_id: UUID, connection_id: UUID
) -> WebchatConnection | None:
    row = (
        await session.execute(
            text(_ACTIVE + "c.tenant_id = :t AND c.id = :id"),
            {"t": tenant_id, "id": connection_id},
        )
    ).first()
    return _row(row)
