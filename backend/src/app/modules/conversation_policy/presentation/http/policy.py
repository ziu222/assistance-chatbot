"""REST chính sách hội thoại: giờ làm việc, tin tự động, mô phỏng (thiết kế bước 2 §12.10)."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.conversation_policy.domain.policy import (
    PolicyConfig,
    PolicyState,
    decide,
    suggested_config,
)
from app.modules.conversation_policy.infrastructure import repository as repo
from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import Csrf, CurrentStaff
from app.shared.presentation.http_errors import ApiError

router = APIRouter(prefix="/api/v1")


class SimulateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: PolicyConfig | None = None  # bản đang sửa (chưa lưu); None ⇒ release hiện hành
    at: AwareDatetime
    is_new_conversation: bool = True
    minutes_since_last_message: int | None = Field(default=None, ge=0)
    handling: Literal["bot", "waiting_human", "human"] = "bot"


class ReturnToBotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


def _require(actor: StaffActor, action: str) -> UUID:
    if not authorize(actor, action, actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    return actor.tenant_id


def _release_json(r: repo.Release) -> dict:
    return {
        "id": str(r.id),
        "number": r.number,
        "created_at": r.created_at.isoformat(),
        "current": r.current,
        "config": repo.config_json(r.config),
    }


@router.get("/policy")
async def get_policy(request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "agent.read")
    async with request.app.state.session_factory() as s:
        release = await repo.current_release(s, tenant_id)
    return {
        "release": _release_json(release) if release else None,
        "suggested": repo.config_json(suggested_config()),
    }


@router.put("/policy", dependencies=[Csrf])
async def save_policy(body: PolicyConfig, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "agent.manage")
    try:
        async with request.app.state.session_factory() as s, s.begin():
            release = await repo.publish(s, tenant_id, body, actor.user_id)
    except IntegrityError as exc:  # hai người lưu cùng lúc: số release trùng
        raise ApiError(409, "conflict", "Someone saved at the same time; reload") from exc
    return _release_json(release)


@router.get("/policy/releases")
async def list_releases(request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "agent.read")
    async with request.app.state.session_factory() as s:
        releases = await repo.list_releases(s, tenant_id)
    return {"items": [_release_json(r) for r in releases]}


@router.post("/policy/releases/{release_id}/restore", dependencies=[Csrf])
async def restore_release(release_id: UUID, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "agent.manage")
    async with request.app.state.session_factory() as s, s.begin():
        if not await repo.restore(s, tenant_id, release_id):
            raise ApiError(404, "not_found", "Release not found")
        release = await repo.current_release(s, tenant_id)
    assert release is not None
    return _release_json(release)


@router.post("/policy/simulate")
async def simulate(body: SimulateIn, request: Request, actor: CurrentStaff) -> dict:
    """Gọi đúng hàm `decide` của worker; không ghi gì. Không cần CSRF vì không đổi trạng thái."""
    tenant_id = _require(actor, "agent.read")
    async with request.app.state.session_factory() as s:
        config = body.config
        if config is None:
            release = await repo.current_release(s, tenant_id)
            config = release.config if release else suggested_config()
        shop = (
            await s.execute(text("SELECT name FROM tenants WHERE id = :t"), {"t": tenant_id})
        ).scalar_one()
    prev: datetime | None = None
    if body.minutes_since_last_message is not None:
        prev = body.at - timedelta(minutes=body.minutes_since_last_message)
    state = PolicyState(body.handling, body.is_new_conversation, prev, {})
    d = decide(config, state, body.at, shop_name=shop)
    return {
        "situation": d.situation,
        "text": d.text,
        "set_handling": d.set_handling,
        "run_agent": d.run_agent,
    }


@router.post("/conversations/{conversation_id}/return-to-bot", dependencies=[Csrf])
async def return_to_bot(
    conversation_id: UUID, body: ReturnToBotIn, request: Request, actor: CurrentStaff
) -> dict:
    tenant_id = _require(actor, "conversation.reply")
    async with request.app.state.session_factory() as s, s.begin():
        ok = await repo.set_handling(
            s, tenant_id, conversation_id, "bot", expected_version=body.version
        )
    if not ok:
        raise ApiError(409, "conflict", "Conversation changed or not found; reload")
    return {"handling": "bot"}
