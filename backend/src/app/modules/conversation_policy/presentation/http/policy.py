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
from app.modules.conversation_policy.infrastructure import handoffs
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
        "auto_send": release.auto_send if release else False,
        "suggested": repo.config_json(suggested_config()),
    }


class PolicySettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_send: bool


@router.patch("/policy/settings", dependencies=[Csrf])
async def update_policy_settings(
    body: PolicySettingsIn, request: Request, actor: CurrentStaff
) -> dict:
    """Bật/tắt gửi tin tự động cho khách. Tắt có hiệu lực ngay với lượt kế tiếp."""
    tenant_id = _require(actor, "agent.manage")
    async with request.app.state.session_factory() as s, s.begin():
        if not await repo.set_auto_send(s, tenant_id, body.auto_send):
            raise ApiError(409, "no_policy", "Save the auto messages first")
    return {"auto_send": body.auto_send}


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


# ---------- ticket "Nhờ nhân viên" (§12.5) ----------


def _ticket_json(t: handoffs.Ticket) -> dict:
    return {
        "id": str(t.id),
        "conversation_id": str(t.conversation_id),
        "status": t.status,
        "source": t.source,
        "reason": t.reason,
        "priority": t.priority,
        "opened_at": t.opened_at.isoformat(),
        "claimed_by": str(t.claimed_by) if t.claimed_by else None,
        "customer_name": t.customer_name,
        "last_message_preview": t.last_message_preview,
        "handling": t.handling,
    }


@router.get("/handoffs")
async def list_handoffs(request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "conversation.read")
    async with request.app.state.session_factory() as s:
        tickets = await handoffs.list_active(s, tenant_id)
    return {"items": [_ticket_json(t) for t in tickets]}


@router.post("/handoffs/{handoff_id}/{action}", dependencies=[Csrf])
async def act_on_handoff(
    handoff_id: UUID,
    action: Literal["claim", "resolve", "dismiss"],
    request: Request,
    actor: CurrentStaff,
) -> dict:
    tenant_id = _require(actor, "conversation.reply")
    async with request.app.state.session_factory() as s, s.begin():
        ok = await handoffs.transition(s, tenant_id, handoff_id, action, actor.user_id)
    if not ok:
        raise ApiError(409, "conflict", "Ticket not found or already handled; reload")
    return {"ok": True}


@router.post("/conversations/{conversation_id}/handoff", dependencies=[Csrf])
async def open_handoff_by_staff(
    conversation_id: UUID, request: Request, actor: CurrentStaff
) -> dict:
    """Nhân viên tự đánh dấu hội thoại cần xử lý (vd đồng nghiệp nhờ xem giúp)."""
    tenant_id = _require(actor, "conversation.reply")
    async with request.app.state.session_factory() as s, s.begin():
        exists = (
            await s.execute(
                text("SELECT 1 FROM conversations WHERE tenant_id = :t AND id = :c"),
                {"t": tenant_id, "c": conversation_id},
            )
        ).first()
        if exists is None:
            raise ApiError(404, "not_found", "Conversation not found")
        handoff_id, created = await handoffs.open_handoff(
            s, tenant_id, conversation_id, source="staff"
        )
    return {"id": str(handoff_id), "created": created}
