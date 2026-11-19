"""Auth nhân viên tối thiểu (thiết kế §8.6): cookie `__Host-sid` DB-backed, argon2id, CSRF."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.modules.identity.domain.authorization import StaffActor
from app.modules.identity.infrastructure import staff_sessions
from app.shared.infrastructure.rate_limit import RateLimitUnavailable, hashed
from app.shared.presentation.http_errors import ApiError

COOKIE = "__Host-sid"
LOGIN_PER_IP = 20  # / phút
LOGIN_PER_EMAIL = 5  # / phút

router = APIRouter(prefix="/api/v1/auth")


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)  # chặn băm chuỗi khổng lồ
    tenant_slug: str | None = Field(default=None, max_length=100)


def require_csrf(request: Request) -> None:
    """Method không an toàn: SameSite=Lax + header tùy chỉnh + Origin đúng admin inbox."""
    origin = (request.headers.get("origin") or "").lower().removesuffix("/")
    expected = request.app.state.settings.staff_origin.lower().removesuffix("/")
    if not request.headers.get("x-requested-with") or origin != expected:
        raise ApiError(403, "csrf_rejected", "Request rejected")


async def current_staff(
    request: Request, sid: Annotated[str | None, Cookie(alias=COOKIE)] = None
) -> StaffActor:
    actor = None
    if sid:
        async with request.app.state.session_factory() as s:
            actor = await staff_sessions.actor_for_session(s, sid)
    if actor is None:
        raise ApiError(401, "unauthenticated", "Login required")
    return actor


CurrentStaff = Annotated[StaffActor, Depends(current_staff)]
Csrf = Depends(require_csrf)


def _me(actor: StaffActor) -> dict:
    return {
        "user": {
            "id": str(actor.user_id),
            "email": actor.email,
            "display_name": actor.display_name,
        },
        "tenant": {
            "id": str(actor.tenant_id),
            "name": actor.tenant_name,
            "slug": actor.tenant_slug,
        },
        "role": actor.role,
    }


async def _limit(request: Request, key: str, limit: int) -> None:
    try:
        retry_after = await request.app.state.rate_limiter.hit(key, limit, fail_open=False)
    except RateLimitUnavailable as exc:
        raise ApiError(503, "rate_limit_unavailable", "Try again later") from exc
    if retry_after is not None:
        raise ApiError(429, "rate_limited", "Too many attempts", {"Retry-After": str(retry_after)})


@router.post("/login", dependencies=[Csrf])
async def login(body: LoginIn, request: Request, response: Response) -> dict:
    email = body.email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    # Đếm trước khi verify: brute-force bị chặn kể cả khi đoán trúng ở lượt vượt ngưỡng.
    await _limit(request, f"login:ip:{hashed(ip)}", LOGIN_PER_IP)
    await _limit(request, f"login:email:{hashed(email)}", LOGIN_PER_EMAIL)

    ttl = request.app.state.settings.staff_session_ttl_seconds
    async with request.app.state.session_factory() as s:
        try:
            actor = await staff_sessions.authenticate(s, email, body.password, body.tenant_slug)
        except staff_sessions.TenantRequired as exc:
            raise ApiError(400, "tenant_required", "Choose a workspace") from exc
        if actor is None:
            # Một thông báo cho mọi trường hợp: không lộ email nào tồn tại.
            raise ApiError(401, "invalid_credentials", "Invalid email or password")
        raw = await staff_sessions.create_session(s, actor, timedelta(seconds=ttl))
        await s.commit()

    response.set_cookie(
        COOKIE, raw, max_age=ttl, httponly=True, secure=True, samesite="lax", path="/"
    )
    return _me(actor)


@router.post("/logout", status_code=204, dependencies=[Csrf])
async def logout(
    request: Request, response: Response, sid: Annotated[str | None, Cookie(alias=COOKIE)] = None
) -> None:
    if sid:
        async with request.app.state.session_factory() as s:
            await staff_sessions.delete_session(s, sid)
            await s.commit()
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")


@router.get("/me")
async def me(actor: CurrentStaff) -> dict:
    return _me(actor)
