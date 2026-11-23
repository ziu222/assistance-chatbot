"""REST phiếu đặt hàng cho nhân viên (thiết kế 03 §5.3, §7): "Đơn chờ xác nhận"."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import Csrf, CurrentStaff
from app.modules.orders.infrastructure.staff_actions import (
    close_request,
    edit_request,
    get_request,
    list_requests,
)
from app.shared.presentation.http_errors import ApiError

router = APIRouter(prefix="/api/v1/order-requests")
Status = Literal["collecting", "ready", "confirmed", "cancelled", "expired"]


def _require(actor: StaffActor, action: str) -> UUID:
    if not authorize(actor, action, actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    return actor.tenant_id


class CustomerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, pattern=r"^0\d{9}$")
    address: str | None = Field(default=None, max_length=300)
    delivery_time: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=300)


class OrderPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer: CustomerPatch | None = None
    quantity: int | None = Field(default=None, ge=1, le=999)
    variant: str | None = Field(default=None, max_length=100)


@router.get("")
async def list_(
    request: Request,
    actor: CurrentStaff,
    status: list[Status] = Query(default=["ready", "collecting"]),  # noqa: B008
) -> dict:
    tenant_id = _require(actor, "conversation.read")
    async with request.app.state.session_factory() as s:
        return {"items": await list_requests(s, tenant_id, status)}


@router.get("/{request_id}")
async def detail(request_id: UUID, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "conversation.read")
    async with request.app.state.session_factory() as s:
        out = await get_request(s, tenant_id, request_id)
    if out is None:
        raise ApiError(404, "not_found", "Not found")
    return out


@router.patch("/{request_id}", dependencies=[Csrf])
async def edit(request_id: UUID, body: OrderPatch, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "conversation.reply")
    async with request.app.state.session_factory() as s, s.begin():
        out = await edit_request(
            s,
            tenant_id,
            request_id,
            # Chỉ trường nhân viên gửi lên; gửi null = xóa trường đó.
            customer=body.customer.model_dump(exclude_unset=True) if body.customer else {},
            quantity=body.quantity,
            variant=body.variant,
        )
    if out is None:
        raise ApiError(409, "conflict", "Order request not found or already closed; reload")
    return out


@router.post("/{request_id}/{action}", dependencies=[Csrf])
async def close(
    request_id: UUID,
    action: Literal["confirm", "cancel"],
    request: Request,
    actor: CurrentStaff,
) -> dict:
    tenant_id = _require(actor, "conversation.reply")
    async with request.app.state.session_factory() as s, s.begin():
        ok = await close_request(s, tenant_id, request_id, action, actor.user_id)
    if not ok:
        raise ApiError(409, "conflict", "Order request not found or already closed; reload")
    return {"ok": True}
