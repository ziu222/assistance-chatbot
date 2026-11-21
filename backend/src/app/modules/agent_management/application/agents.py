"""Use case quản lý agent (bước 2.2)."""

import json
import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_management.infrastructure import repository as repo
from app.modules.agent_management.infrastructure.templates import get_template

log = logging.getLogger(__name__)


class TemplateNotFound(Exception):
    pass


class ConnectionNotFound(Exception):
    pass


class AgentExists(Exception):
    pass


class AgentNotFound(Exception):
    pass


class VersionConflict(Exception):
    pass


async def create_agent(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    actor_id: UUID,
    template_key: str,
    template_version: int | None,
    connection_id: UUID,
    name: str,
) -> repo.AgentRow:
    """Tạo agent từ template ở mode `manual`: admin phải chủ động bật Shadow."""
    try:
        template = get_template(template_key, template_version)
    except KeyError as exc:
        raise TemplateNotFound from exc
    if not await repo.connection_exists(s, tenant_id, connection_id):
        raise ConnectionNotFound
    agent_id = await repo.insert_agent(
        s,
        tenant_id=tenant_id,
        connection_id=connection_id,
        name=name,
        template_key=template.key,
        template_version=template.version,
        config=json.loads(template.model_dump_json()),  # ảnh chụp, không tham chiếu file
        created_by=actor_id,
    )
    if agent_id is None:
        raise AgentExists
    agent = await repo.get_agent(s, tenant_id, agent_id)
    assert agent is not None
    return agent


async def update_agent(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    actor_id: UUID,
    agent_id: UUID,
    expected_version: int,
    mode: str | None,
    shadow_sample_rate: Decimal | None,
) -> repo.AgentRow:
    before = await repo.get_agent(s, tenant_id, agent_id)
    if before is None:
        raise AgentNotFound
    updated = await repo.update_agent(
        s,
        tenant_id,
        agent_id,
        expected_version=expected_version,
        mode=mode,
        shadow_sample_rate=shadow_sample_rate,
    )
    if not updated:
        raise VersionConflict
    after = await repo.get_agent(s, tenant_id, agent_id)
    assert after is not None
    if mode and mode != before.mode:
        # ponytail: chưa có audit_events (nợ có chủ đích, thiết kế bước 1 §5); log có cấu trúc.
        log.info(
            "agent mode changed",
            extra={
                "agent_id": str(agent_id),
                "from": before.mode,
                "to": mode,
                "actor_id": str(actor_id),
            },
        )
    return after
