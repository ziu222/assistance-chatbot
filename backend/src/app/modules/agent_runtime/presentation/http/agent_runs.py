"""REST đọc run của agent cho trace view (thiết kế bước 2 §8.1). Chỉ đọc."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.modules.agent_runtime.infrastructure import run_queries as queries
from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import CurrentStaff
from app.shared.presentation.http_errors import ApiError

router = APIRouter(prefix="/api/v1")

Status = Annotated[
    str | None,
    Query(pattern="^(running|completed|handoff_suggested|skipped|superseded|failed|timed_out)$"),
]


def _tenant(actor: StaffActor) -> UUID:
    if not authorize(actor, "agent.read", actor.tenant_id):  # nhân viên thường cũng được đọc
        raise ApiError(403, "forbidden", "Not allowed")
    return actor.tenant_id


@router.get("/agent-runs")
async def list_runs(
    request: Request,
    actor: CurrentStaff,
    conversation_id: UUID | None = None,
    status: Status = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    tenant_id = _tenant(actor)
    async with request.app.state.session_factory() as s:
        try:
            items, next_cursor = await queries.list_runs(
                s,
                tenant_id,
                conversation_id=conversation_id,
                status=status,
                cursor=cursor,
                limit=limit,
            )
        except queries.InvalidCursor as exc:
            raise ApiError(422, "invalid_request", "Invalid cursor") from exc
    return {"items": items, "next_cursor": next_cursor}


@router.get("/agent-runs/{run_id}")
async def get_run(run_id: UUID, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _tenant(actor)
    async with request.app.state.session_factory() as s:
        run = await queries.get_run(s, tenant_id, run_id)
    if run is None:
        raise ApiError(404, "not_found", "Run not found")
    return run
