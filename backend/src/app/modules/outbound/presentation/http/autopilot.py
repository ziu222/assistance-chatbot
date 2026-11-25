"""REST quản lý Autopilot theo từng ý định và nút dừng khẩn cấp (thiết kế 06 A2)."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import Csrf, CurrentStaff
from app.modules.outbound.infrastructure import autopilot as repo
from app.shared.presentation.http_errors import ApiError

router = APIRouter(prefix="/api/v1/autopilot")


class EnableIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: UUID | None = None
    source: Literal["blind", "shown"] | None = None
    days: int = Field(default=14, ge=1, le=90)


class DisableIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: UUID | None = None


def _require(actor: StaffActor, action: str) -> UUID:
    if not authorize(actor, action, actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    return actor.tenant_id


@router.get("/intents")
async def get_intents(
    request: Request,
    actor: CurrentStaff,
    agent_id: Annotated[UUID | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> dict:
    tenant_id = _require(actor, "agent.read")
    async with request.app.state.session_factory() as s:
        return await repo.list_autopilot_intents(s, tenant_id, agent_id=agent_id, days=days)


@router.post("/intents/{intent}/enable", dependencies=[Csrf])
async def enable(
    intent: str,
    request: Request,
    actor: CurrentStaff,
    body: EnableIn | None = None,
) -> dict:
    tenant_id = _require(actor, "autopilot.manage")
    b = body or EnableIn()
    try:
        async with request.app.state.session_factory() as s, s.begin():
            return await repo.enable_intent(
                s,
                tenant_id,
                intent,
                user_id=actor.user_id,
                agent_id=b.agent_id,
                preferred_source=b.source,
                days=b.days,
            )
    except repo.AgentNotFound as exc:
        raise ApiError(404, "not_found", "Agent not found") from exc
    except repo.AutopilotNotEligible as exc:
        raise ApiError(
            422,
            "not_eligible",
            exc.reasons[0] if exc.reasons else "Chưa đủ điều kiện bật tự trả lời",
            details={"reasons": exc.reasons},
        ) from exc


@router.post("/intents/{intent}/disable", dependencies=[Csrf])
async def disable(
    intent: str,
    request: Request,
    actor: CurrentStaff,
    body: DisableIn | None = None,
) -> dict:
    tenant_id = _require(actor, "autopilot.manage")
    b = body or DisableIn()
    async with request.app.state.session_factory() as s, s.begin():
        item = await repo.disable_intent(
            s, tenant_id, intent, user_id=actor.user_id, agent_id=b.agent_id
        )
    if item is None:
        raise ApiError(404, "not_found", "Autopilot intent not found")
    return item


@router.post("/pause", dependencies=[Csrf])
async def pause(request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "autopilot.manage")
    async with request.app.state.session_factory() as s, s.begin():
        return await repo.pause_autopilot(s, tenant_id, user_id=actor.user_id)
