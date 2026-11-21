"""Truy cập dữ liệu agent: SQL thẳng, mọi câu lọc tenant_id."""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_DAILY_COST_CAP_MICRO = 2_000_000


@dataclass(frozen=True)
class AgentRow:
    id: UUID
    tenant_id: UUID
    channel_connection_id: UUID
    name: str
    template_key: str
    template_version: int
    mode: str
    shadow_sample_rate: Decimal
    version: int
    current_version_id: UUID | None
    current_version_number: int | None
    current_version_created_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AiSettings:
    ai_enabled: bool
    daily_cost_cap_micro: int


_SELECT = (
    "SELECT a.id, a.tenant_id, a.channel_connection_id, a.name, a.template_key, "
    "a.template_version, a.mode, a.shadow_sample_rate, a.version, a.current_version_id, "
    "v.version_number AS current_version_number, v.created_at AS current_version_created_at, "
    "a.created_at, a.updated_at "
    "FROM agents a LEFT JOIN agent_versions v ON v.id = a.current_version_id "
    "WHERE a.tenant_id = :t "
)


async def connection_exists(s: AsyncSession, tenant_id: UUID, connection_id: UUID) -> bool:
    row = await s.execute(
        text("SELECT 1 FROM channel_connections WHERE tenant_id = :t AND id = :c"),
        {"t": tenant_id, "c": connection_id},
    )
    return row.first() is not None


async def insert_agent(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    connection_id: UUID,
    name: str,
    template_key: str,
    template_version: int,
    config: dict,
    created_by: UUID,
) -> UUID | None:
    """Tạo agent + version 1 (ảnh chụp cấu hình). None nếu kênh đã có agent."""
    agent_id, version_id = uuid4(), uuid4()
    inserted = (
        await s.execute(
            text(
                "INSERT INTO agents (id, tenant_id, channel_connection_id, name, template_key, "
                "template_version, current_version_id) VALUES (:id, :t, :c, :n, :k, :kv, :vid) "
                "ON CONFLICT (tenant_id, channel_connection_id) DO NOTHING RETURNING id"
            ),
            {
                "id": agent_id,
                "t": tenant_id,
                "c": connection_id,
                "n": name,
                "k": template_key,
                "kv": template_version,
                "vid": version_id,
            },
        )
    ).first()
    if inserted is None:
        return None
    await s.execute(
        text(
            "INSERT INTO agent_versions (id, tenant_id, agent_id, version_number, config, "
            "created_by) VALUES (:id, :t, :a, 1, CAST(:cfg AS jsonb), :by)"
        ),
        {
            "id": version_id,
            "t": tenant_id,
            "a": agent_id,
            "cfg": json.dumps(config, ensure_ascii=False),
            "by": created_by,
        },
    )
    return agent_id


async def get_agent(s: AsyncSession, tenant_id: UUID, agent_id: UUID) -> AgentRow | None:
    row = (
        await s.execute(text(_SELECT + "AND a.id = :id"), {"t": tenant_id, "id": agent_id})
    ).first()
    return AgentRow(*row) if row else None


async def list_agents(s: AsyncSession, tenant_id: UUID) -> list[AgentRow]:
    rows = await s.execute(text(_SELECT + "ORDER BY a.created_at"), {"t": tenant_id})
    return [AgentRow(*r) for r in rows]


async def update_agent(
    s: AsyncSession,
    tenant_id: UUID,
    agent_id: UUID,
    *,
    expected_version: int,
    mode: str | None,
    shadow_sample_rate: Decimal | None,
) -> bool:
    """Cập nhật nếu version khớp (optimistic concurrency). False nếu không khớp/không có."""
    result = await s.execute(
        text(
            # CAST tường minh: tham số None khiến asyncpg đoán sai kiểu trong COALESCE.
            "UPDATE agents SET mode = COALESCE(CAST(:mode AS text), mode), "
            "shadow_sample_rate = COALESCE(CAST(:rate AS numeric), shadow_sample_rate), "
            "version = version + 1, updated_at = now() "
            "WHERE tenant_id = :t AND id = :id AND version = :v"
        ),
        {
            "mode": mode,
            "rate": shadow_sample_rate,
            "t": tenant_id,
            "id": agent_id,
            "v": expected_version,
        },
    )
    return result.rowcount == 1


async def get_ai_settings(s: AsyncSession, tenant_id: UUID) -> AiSettings:
    row = (
        await s.execute(
            text(
                "SELECT ai_enabled, daily_cost_cap_micro FROM tenant_ai_settings "
                "WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        )
    ).first()
    # Chưa có hàng = mặc định an toàn: AI TẮT.
    return AiSettings(*row) if row else AiSettings(False, DEFAULT_DAILY_COST_CAP_MICRO)


async def upsert_ai_settings(
    s: AsyncSession, tenant_id: UUID, *, ai_enabled: bool | None, daily_cost_cap_micro: int | None
) -> AiSettings:
    row = (
        await s.execute(
            text(
                "INSERT INTO tenant_ai_settings (tenant_id, ai_enabled, daily_cost_cap_micro) "
                "VALUES (:t, COALESCE(CAST(:on AS boolean), false), "
                "COALESCE(CAST(:cap AS bigint), :default_cap)) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "ai_enabled = COALESCE(CAST(:on AS boolean), tenant_ai_settings.ai_enabled), "
                "daily_cost_cap_micro = "
                "COALESCE(CAST(:cap AS bigint), tenant_ai_settings.daily_cost_cap_micro), "
                "updated_at = now() "
                "RETURNING ai_enabled, daily_cost_cap_micro"
            ),
            {
                "t": tenant_id,
                "on": ai_enabled,
                "cap": daily_cost_cap_micro,
                "default_cap": DEFAULT_DAILY_COST_CAP_MICRO,
            },
        )
    ).one()
    return AiSettings(*row)
