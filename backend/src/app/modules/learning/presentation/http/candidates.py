"""REST ứng viên tri thức "Bot muốn học" (thiết kế 05 §6.3, L4): xem, duyệt/sửa, bỏ."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import Csrf, CurrentStaff
from app.modules.learning.infrastructure.review import (
    CandidateBlocked,
    approve_candidate,
    list_candidates,
    reject_candidate,
)
from app.shared.presentation.http_errors import ApiError

router = APIRouter(prefix="/api/v1/learning")

StatusFilter = Literal["pending", "approved", "eval_failed", "published", "rejected"]


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str | None = Field(default=None, min_length=1, max_length=500)
    answer: str | None = Field(default=None, min_length=1, max_length=4000)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=20000)


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=500)


def _require(actor: StaffActor, action: str) -> UUID:
    if not authorize(actor, action, actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    return actor.tenant_id


@router.get("/candidates")
async def get_candidates(
    request: Request,
    actor: CurrentStaff,
    status: Annotated[StatusFilter | None, Query()] = None,
) -> dict:
    tenant_id = _require(actor, "knowledge.read")
    async with request.app.state.session_factory() as s:
        items = await list_candidates(s, tenant_id, status=status)
    return {"items": items}


@router.post("/candidates/{candidate_id}/approve", dependencies=[Csrf])
async def approve(
    candidate_id: UUID,
    request: Request,
    actor: CurrentStaff,
    body: ApproveIn | None = None,
) -> dict:
    tenant_id = _require(actor, "knowledge.approve")
    edited = body.model_dump(exclude_none=True) if body is not None else None
    if edited == {}:
        edited = None
    try:
        async with request.app.state.session_factory() as s, s.begin():
            item = await approve_candidate(
                s, tenant_id, candidate_id, reviewer_id=actor.user_id, edited_proposal=edited
            )
    except CandidateBlocked as exc:
        raise ApiError(
            422, "candidate_blocked", "Ứng viên chưa đạt kiểm tra sự thật/PII/chữ khách", exc.checks
        ) from exc
    if item is None:
        raise ApiError(404, "not_found", "Candidate not found or already reviewed")
    return item


@router.post("/candidates/{candidate_id}/reject", dependencies=[Csrf])
async def reject(
    candidate_id: UUID,
    request: Request,
    actor: CurrentStaff,
    body: RejectIn | None = None,
) -> dict:
    tenant_id = _require(actor, "knowledge.approve")
    reason = body.reason if body is not None else None
    async with request.app.state.session_factory() as s, s.begin():
        item = await reject_candidate(
            s, tenant_id, candidate_id, reviewer_id=actor.user_id, reason=reason
        )
    if item is None:
        raise ApiError(404, "not_found", "Candidate not found or already reviewed")
    return item
