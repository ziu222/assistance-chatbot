"""REST quản lý kết nối kênh bán hàng (O5): liệt kê, kết nối Messenger, bật/tắt, xoay vòng token."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import Csrf, CurrentStaff
from app.modules.integration.domain.channels import InvalidChannelInput, build_webhook_url
from app.modules.integration.infrastructure import channels as repo
from app.shared.infrastructure.crypto import CredentialsKeyNotConfigured
from app.shared.presentation.http_errors import ApiError

router = APIRouter(prefix="/api/v1/channels")


class CreateMessengerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: str = Field(max_length=256)
    page_access_token: str = Field(max_length=2048)
    app_secret: str = Field(max_length=2048)
    verify_token: str = Field(max_length=2048)


class UpdateChannelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["active", "disabled"] | None = None
    page_access_token: str | None = Field(default=None, max_length=2048)
    app_secret: str | None = Field(default=None, max_length=2048)
    verify_token: str | None = Field(default=None, max_length=2048)


def _require(actor: StaffActor, action: str) -> UUID:
    if not authorize(actor, action, actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    return actor.tenant_id


def _channel_json(c: repo.ChannelSummary, public_origin: str) -> dict:
    return {
        "id": str(c.id),
        "provider": c.provider,
        "external_account_id": c.external_account_id,
        "status": c.status,
        "public_key": c.public_key,
        "has_credentials": c.has_credentials,
        "webhook_url": build_webhook_url(public_origin, c.provider),
        "created_at": c.created_at.isoformat(),
    }


@router.get("")
async def list_channels(request: Request, actor: CurrentStaff) -> dict:
    """Liệt kê các kết nối kênh của tenant (không trả token/secret)."""
    tenant_id = _require(actor, "channels.manage")
    settings = request.app.state.settings
    async with request.app.state.session_factory() as s:
        items = await repo.list_channels(s, tenant_id)
    return {
        "items": [_channel_json(c, settings.public_origin) for c in items],
        "credentials_configured": bool((settings.channel_credentials_key or "").strip()),
    }


@router.post("/messenger", dependencies=[Csrf])
async def create_messenger_channel(
    body: CreateMessengerIn, request: Request, actor: CurrentStaff
) -> JSONResponse:
    """Tạo kết nối Facebook Messenger mới với credentials được mã hóa trước khi lưu."""
    tenant_id = _require(actor, "channels.manage")
    settings = request.app.state.settings
    try:
        async with request.app.state.session_factory() as s, s.begin():
            created = await repo.create_messenger_channel(
                s,
                tenant_id=tenant_id,
                page_id=body.page_id,
                page_access_token=body.page_access_token,
                app_secret=body.app_secret,
                verify_token=body.verify_token,
                credentials_key=settings.channel_credentials_key,
            )
    except InvalidChannelInput as exc:
        raise ApiError(422, "invalid_request", str(exc)) from exc
    except CredentialsKeyNotConfigured as exc:
        raise ApiError(
            503,
            "credentials_key_missing",
            "Channel credentials encryption key is not configured",
        ) from exc
    except repo.ChannelAlreadyExists as exc:
        raise ApiError(
            409,
            "channel_exists",
            "This Facebook Page is already connected and active",
        ) from exc
    return JSONResponse(_channel_json(created, settings.public_origin), status_code=201)


@router.patch("/{connection_id}", dependencies=[Csrf])
async def update_channel(
    connection_id: UUID,
    body: UpdateChannelIn,
    request: Request,
    actor: CurrentStaff,
) -> dict:
    """Đổi trạng thái `active`/`disabled` hoặc xoay vòng token/secret cho kết nối kênh."""
    tenant_id = _require(actor, "channels.manage")
    settings = request.app.state.settings
    try:
        async with request.app.state.session_factory() as s, s.begin():
            updated = await repo.update_channel(
                s,
                tenant_id=tenant_id,
                connection_id=connection_id,
                status=body.status,
                page_access_token=body.page_access_token,
                app_secret=body.app_secret,
                verify_token=body.verify_token,
                credentials_key=settings.channel_credentials_key,
            )
    except repo.ChannelNotFound as exc:
        raise ApiError(404, "not_found", "Channel connection not found") from exc
    except InvalidChannelInput as exc:
        raise ApiError(422, "invalid_request", str(exc)) from exc
    except CredentialsKeyNotConfigured as exc:
        raise ApiError(
            503,
            "credentials_key_missing",
            "Channel credentials encryption key is not configured",
        ) from exc
    except repo.ChannelAlreadyExists as exc:
        raise ApiError(
            409,
            "channel_exists",
            "This Facebook Page is already connected and active",
        ) from exc
    return _channel_json(updated, settings.public_origin)
