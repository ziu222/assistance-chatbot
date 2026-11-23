"""REST trợ lý viết và hồ sơ thương hiệu (thiết kế bước 2 §12.8, §12.10).

Gợi ý chạy bất đồng bộ: API chỉ ghi yêu cầu (không giữ key LLM); agent worker xử lý; giao diện
hỏi lại kết quả. Gợi ý không bao giờ tự lưu vào nội dung của shop.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from app.modules.authoring.domain.writing import BrandProfile, Kind, Level
from app.modules.authoring.infrastructure import repository as repo
from app.modules.conversation_policy.infrastructure.handoffs import record_signal
from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import Csrf, CurrentStaff
from app.shared.presentation.http_errors import ApiError

router = APIRouter(prefix="/api/v1")


class SuggestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind = "other"
    level: Level = "polish"
    text: str = Field(min_length=1, max_length=2000)


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0, le=2)
    action: Literal["used", "dismissed"]


def _require(actor: StaffActor, action: str) -> UUID:
    if not authorize(actor, action, actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    return actor.tenant_id


def _request_json(r: repo.WritingRequest) -> dict:
    return {
        "id": str(r.id),
        "kind": r.kind,
        "level": r.level,
        "status": r.status,
        "suggestions": r.suggestions or [],
        "error_code": r.error_code,
    }


@router.post("/writing/suggest", dependencies=[Csrf])
async def suggest(body: SuggestIn, request: Request, actor: CurrentStaff) -> JSONResponse:
    tenant_id = _require(actor, "agent.manage")
    if not body.text.strip():
        raise ApiError(422, "invalid_request", "Text is empty")
    async with request.app.state.session_factory() as s, s.begin():
        rid = await repo.create_request(
            s, tenant_id, kind=body.kind, level=body.level, text_=body.text,
            created_by=actor.user_id,
        )  # fmt: skip
    if rid is None:
        raise ApiError(429, "too_many_requests", "Wait for the previous suggestions")
    return JSONResponse({"id": str(rid), "status": "pending"}, status_code=202)


@router.get("/writing/requests/{request_id}")
async def get_suggestions(request_id: UUID, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "agent.manage")
    async with request.app.state.session_factory() as s:
        found = await repo.get_request(s, tenant_id, request_id)
    if found is None:
        raise ApiError(404, "not_found", "Request not found")
    return _request_json(found)


@router.post("/writing/requests/{request_id}/feedback", dependencies=[Csrf])
async def feedback(
    request_id: UUID, body: FeedbackIn, request: Request, actor: CurrentStaff
) -> dict:
    """Ghi "Dùng"/"Bỏ" vào tín hiệu học (§12.9). Không lưu nội dung gợi ý vào đâu khác."""
    tenant_id = _require(actor, "agent.manage")
    async with request.app.state.session_factory() as s, s.begin():
        found = await repo.get_request(s, tenant_id, request_id)
        if found is None or found.status != "done" or body.index >= len(found.suggestions or []):
            raise ApiError(404, "not_found", "Suggestion not found")
        fact_ok = found.suggestions[body.index]["fact_check"]["ok"]  # type: ignore[index]
        if body.action == "used" and not fact_ok:
            raise ApiError(409, "facts_changed", "This suggestion changes facts; edit it first")
        await record_signal(
            s, tenant_id, None, f"suggestion_{body.action}",
            {"request_id": str(request_id), "index": body.index, "level": found.level,
             "kind": found.kind, "fact_ok": fact_ok},
        )  # fmt: skip
    return {"ok": True}


@router.get("/brand-profile")
async def get_brand_profile(request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "agent.read")
    async with request.app.state.session_factory() as s:
        found = await repo.current_profile(s, tenant_id)
    number, profile = found if found else (None, BrandProfile())
    return {"number": number, "profile": profile.model_dump()}


@router.put("/brand-profile", dependencies=[Csrf])
async def save_brand_profile(body: BrandProfile, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "agent.manage")
    try:
        async with request.app.state.session_factory() as s, s.begin():
            number = await repo.save_profile(s, tenant_id, body, actor.user_id)
    except IntegrityError as exc:
        raise ApiError(409, "conflict", "Someone saved at the same time; reload") from exc
    return {"number": number, "profile": body.model_dump()}
