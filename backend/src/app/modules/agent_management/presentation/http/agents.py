"""REST quản lý agent + kill switch AI của tenant (thiết kế bước 2 §8.1)."""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.modules.agent_management.application import agents as uc
from app.modules.agent_management.infrastructure import repository as repo
from app.modules.agent_management.infrastructure.templates import load_templates
from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import Csrf, CurrentStaff
from app.shared.presentation.http_errors import ApiError

router = APIRouter(prefix="/api/v1")


class CreateAgentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_key: str = Field(min_length=1, max_length=63)
    template_version: int | None = Field(default=None, ge=1)
    channel_connection_id: UUID
    name: str = Field(min_length=1, max_length=100)


class UpdateAgentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)  # bắt buộc: chống ghi đè khi hai admin sửa cùng lúc
    mode: Literal["manual", "shadow", "learning"] | None = None  # chưa có autopilot (bước 5)
    shadow_sample_rate: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=2)


class AiSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ai_enabled: bool | None = None
    daily_cost_cap_micro: int | None = Field(default=None, ge=0)


def _require(actor: StaffActor, action: str) -> UUID:
    if not authorize(actor, action, actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    return actor.tenant_id


def _agent_json(a: repo.AgentRow) -> dict:
    return {
        "id": str(a.id),
        "name": a.name,
        "channel_connection_id": str(a.channel_connection_id),
        "template_key": a.template_key,
        "template_version": a.template_version,
        "mode": a.mode,
        "shadow_sample_rate": float(a.shadow_sample_rate),
        "version": a.version,
        "current_version": {
            "id": str(a.current_version_id),
            "version_number": a.current_version_number,
            "created_at": a.current_version_created_at.isoformat()
            if a.current_version_created_at
            else None,
        },
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
    }


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Agent not found")


@router.get("/agent-templates")
async def list_templates(actor: CurrentStaff) -> dict:
    _require(actor, "agent.read")
    return {
        "items": [
            {
                "key": t.key,
                "version": t.version,
                "default_mode": t.default_mode,
                "intents": t.intents,
                "tools": t.tools,
            }
            for t in load_templates().values()
        ]
    }


@router.get("/channel-connections")
async def list_connections(request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "agent.read")
    async with request.app.state.session_factory() as s:
        return {"items": await repo.list_connections(s, tenant_id)}


@router.get("/agents")
async def list_agents(request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "agent.read")
    async with request.app.state.session_factory() as s:
        agents = await repo.list_agents(s, tenant_id)
    return {"items": [_agent_json(a) for a in agents]}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: UUID, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "agent.read")
    async with request.app.state.session_factory() as s:
        agent = await repo.get_agent(s, tenant_id, agent_id)
    if agent is None:
        raise _not_found()
    return _agent_json(agent)


@router.post("/agents", dependencies=[Csrf])
async def create_agent(body: CreateAgentIn, request: Request, actor: CurrentStaff) -> JSONResponse:
    tenant_id = _require(actor, "agent.manage")
    async with request.app.state.session_factory() as s, s.begin():
        try:
            agent = await uc.create_agent(
                s,
                tenant_id=tenant_id,
                actor_id=actor.user_id,
                template_key=body.template_key,
                template_version=body.template_version,
                connection_id=body.channel_connection_id,
                name=body.name,
            )
        except uc.TemplateNotFound as exc:
            raise ApiError(404, "template_not_found", "Template not found") from exc
        except uc.ConnectionNotFound as exc:
            raise ApiError(404, "not_found", "Channel connection not found") from exc
        except uc.AgentExists as exc:
            raise ApiError(409, "agent_exists", "This channel already has an agent") from exc
    return JSONResponse(_agent_json(agent), status_code=201)


@router.patch("/agents/{agent_id}", dependencies=[Csrf])
async def update_agent(
    agent_id: UUID, body: UpdateAgentIn, request: Request, actor: CurrentStaff
) -> dict:
    tenant_id = _require(actor, "agent.manage")
    async with request.app.state.session_factory() as s, s.begin():
        try:
            agent = await uc.update_agent(
                s,
                tenant_id=tenant_id,
                actor_id=actor.user_id,
                agent_id=agent_id,
                expected_version=body.version,
                mode=body.mode,
                shadow_sample_rate=body.shadow_sample_rate,
            )
        except uc.AgentNotFound as exc:
            raise _not_found() from exc
        except uc.VersionConflict as exc:
            raise ApiError(409, "version_conflict", "Agent was changed; reload and retry") from exc
    return _agent_json(agent)


@router.get("/ai-settings")
async def get_ai_settings(request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "agent.read")
    async with request.app.state.session_factory() as s:
        settings = await repo.get_ai_settings(s, tenant_id)
    return {
        "ai_enabled": settings.ai_enabled,
        "daily_cost_cap_micro": settings.daily_cost_cap_micro,
    }


@router.patch("/ai-settings", dependencies=[Csrf])
async def update_ai_settings(body: AiSettingsIn, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "agent.manage")
    async with request.app.state.session_factory() as s, s.begin():
        settings = await repo.upsert_ai_settings(
            s, tenant_id, ai_enabled=body.ai_enabled, daily_cost_cap_micro=body.daily_cost_cap_micro
        )
    return {
        "ai_enabled": settings.ai_enabled,
        "daily_cost_cap_micro": settings.daily_cost_cap_micro,
    }
