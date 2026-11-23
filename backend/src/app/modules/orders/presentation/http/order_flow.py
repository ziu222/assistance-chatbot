"""REST quy trình đặt hàng (thiết kế 03 §4.2, §7): xem, phát hành, khôi phục."""

from uuid import UUID

from fastapi import APIRouter, Request

from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import Csrf, CurrentStaff
from app.modules.orders.domain.order_flow import OrderFlowConfig
from app.modules.orders.infrastructure.repository import (
    current_flow,
    list_releases,
    publish_flow,
    restore_flow,
)
from app.shared.presentation.http_errors import ApiError

router = APIRouter(prefix="/api/v1/order-flow")


def _require(actor: StaffActor, action: str) -> UUID:
    if not authorize(actor, action, actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    return actor.tenant_id


async def _state(request: Request, tenant_id: UUID) -> dict:
    async with request.app.state.session_factory() as s:
        current = await current_flow(s, tenant_id)
        releases = await list_releases(s, tenant_id)
    return {
        # Chưa phát hành lần nào ⇒ trả bản mặc định (chưa bật) để màn hình có sẵn giá trị gợi ý.
        "current": current.number if current else None,
        "config": (current.config if current else OrderFlowConfig(enabled=False)).model_dump(),
        "releases": releases,
    }


@router.get("")
async def get_flow(request: Request, actor: CurrentStaff) -> dict:
    return await _state(request, _require(actor, "knowledge.read"))


@router.post("/releases", dependencies=[Csrf])
async def publish(body: OrderFlowConfig, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "knowledge.manage")
    async with request.app.state.session_factory() as s, s.begin():
        await publish_flow(s, tenant_id, body, actor.user_id)
    return await _state(request, tenant_id)


@router.post("/releases/{number}/restore", dependencies=[Csrf])
async def restore(number: int, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "knowledge.manage")
    async with request.app.state.session_factory() as s, s.begin():
        if not await restore_flow(s, tenant_id, number):
            raise ApiError(404, "not_found", "Not found")
    return await _state(request, tenant_id)
