"""API public cho widget webchat (thiết kế §7.1).

Ranh giới bảo mật là token ký + rate limit. Origin chỉ chống nhúng/gọi trái phép từ trình
duyệt: allowlist của tenant được ép bằng `frame-ancestors` ở trang iframe; API chỉ nhận
request từ chính origin của iframe (`public_origin`).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.modules.conversation.application.receive_inbound_message import ReceiveInboundMessage
from app.modules.conversation.infrastructure.persistence.repository import (
    ConversationRepository,
    MessageRepository,
)
from app.modules.conversation.infrastructure.unit_of_work import SqlConversationUnitOfWork
from app.modules.customer.infrastructure.persistence.repository import CustomerRepository
from app.modules.integration.domain.channel_event import NormalizedChannelEvent
from app.modules.integration.domain.normalization import (
    InvalidInboundMessage,
    normalize_text,
    validate_client_message_id,
)
from app.modules.integration.domain.origin import frame_ancestors
from app.modules.integration.infrastructure.webchat import connections
from app.modules.integration.infrastructure.webchat.token import (
    InvalidToken,
    TokenSigner,
    VisitorClaims,
)
from app.shared.application.errors import TransientDatabaseError
from app.shared.domain.tenant import TenantContext
from app.shared.infrastructure.rate_limit import RateLimitUnavailable, hashed
from app.shared.presentation.http_errors import ApiError
from app.shared.presentation.sse import sse_response

router = APIRouter()

# (giới hạn / phút) — thiết kế §8.3
SESSIONS_PER_IP = 10
MESSAGES_PER_VISITOR = 20
MESSAGES_PER_IP = 120
MESSAGES_PER_CONNECTION = 1000
STREAMS_PER_VISITOR = 3  # kết nối SSE đồng thời

IFRAME_HTML = (
    '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<title>Chat</title></head><body><div id="root"></div></body></html>'
)  # ponytail: khung rỗng; bundle widget gắn vào ở bước 1.8


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_key: str = Field(min_length=1, max_length=64)
    token: str | None = Field(default=None, max_length=2048)


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_message_id: str = Field(max_length=64)
    text: str = Field(max_length=16_000)  # giới hạn thật (4000) áp sau chuẩn hóa


@dataclass(frozen=True)
class Visitor:
    claims: VisitorClaims
    connection: connections.WebchatConnection

    @property
    def ctx(self) -> TenantContext:
        return TenantContext(self.connection.tenant_id)


def _signer(request: Request) -> TokenSigner:
    signer = request.app.state.token_signer
    if signer is None:
        raise ApiError(503, "webchat_not_configured", "Webchat is not configured")
    return signer


def _client_ip(request: Request) -> str:
    # ponytail: IP trực tiếp; sau reverse proxy cần bật proxy headers của uvicorn.
    return request.client.host if request.client else "unknown"


async def _limit(request: Request, key: str, limit: int, *, fail_open: bool) -> None:
    try:
        retry_after = await request.app.state.rate_limiter.hit(key, limit, fail_open=fail_open)
    except RateLimitUnavailable as exc:
        raise ApiError(
            503, "rate_limit_unavailable", "Try again later", {"Retry-After": "5"}
        ) from exc
    if retry_after is not None:
        raise ApiError(429, "rate_limited", "Too many requests", {"Retry-After": str(retry_after)})


async def current_visitor(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Visitor:
    unauthorized = ApiError(
        401, "invalid_token", "Invalid or expired token", {"WWW-Authenticate": "Bearer"}
    )
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized
    try:
        claims = _signer(request).verify(token.strip())
    except InvalidToken as exc:
        raise unauthorized from exc
    async with request.app.state.session_factory() as s:
        conn = await connections.by_id(s, claims.tenant_id, claims.connection_id)
    if conn is None:  # connection bị tắt / tenant bị khóa sau khi cấp token
        raise unauthorized
    return Visitor(claims, conn)


CurrentVisitor = Annotated[Visitor, Depends(current_visitor)]


@router.get("/w/{public_key}", response_class=HTMLResponse)
async def iframe_page(public_key: str, request: Request) -> HTMLResponse:
    async with request.app.state.session_factory() as s:
        conn = await connections.by_public_key(s, public_key)
    if conn is None or not conn.allowed_origins:  # allowlist rỗng => từ chối (fail closed)
        raise ApiError(404, "not_found", "Widget not found")
    csp = (
        "default-src 'self'; connect-src 'self'; "
        f"frame-ancestors {frame_ancestors(conn.allowed_origins)}"
    )
    return HTMLResponse(
        IFRAME_HTML,
        headers={
            "Content-Security-Policy": csp,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache",  # allowlist đổi phải có hiệu lực ngay
        },
    )


@router.post("/public/webchat/v1/sessions")
async def create_session(body: SessionIn, request: Request) -> dict:
    ip = hashed(_client_ip(request))
    await _limit(request, f"sess:ip:{ip}", SESSIONS_PER_IP, fail_open=False)

    origin = (request.headers.get("origin") or "").lower().removesuffix("/")
    if origin != request.app.state.settings.public_origin.lower().removesuffix("/"):
        raise ApiError(403, "origin_not_allowed", "Origin not allowed")

    signer = _signer(request)
    async with request.app.state.session_factory() as s:
        conn = await connections.by_public_key(s, body.public_key)
    if conn is None:
        raise ApiError(404, "not_found", "Widget not found")

    visitor_id = None
    if body.token:
        try:
            old = signer.verify(body.token)
            if (old.tenant_id, old.connection_id) == (conn.tenant_id, conn.id):
                visitor_id = old.visitor_id  # gia hạn, giữ lịch sử
        except InvalidToken:
            pass  # hết hạn/giả => visitor mới (thiết kế §7.1)

    # Không ghi DB: customer chỉ được tạo khi có tin đầu tiên (D10).
    token, expires_at = signer.issue(conn.tenant_id, conn.id, visitor_id or str(uuid4()))
    return {"token": token, "expires_at": expires_at.isoformat(), "config": conn.public_config}


@router.post("/public/webchat/v1/messages")
async def post_message(body: MessageIn, request: Request, visitor: CurrentVisitor) -> JSONResponse:
    ip = hashed(_client_ip(request))
    # Redis lỗi => fail open có giới hạn cục bộ: không làm sập chat vì Redis.
    for key, limit in (
        (f"msg:v:{visitor.claims.visitor_id}", MESSAGES_PER_VISITOR),
        (f"msg:ip:{ip}", MESSAGES_PER_IP),
        (f"msg:c:{visitor.connection.id}", MESSAGES_PER_CONNECTION),
    ):
        await _limit(request, key, limit, fail_open=True)

    try:
        client_message_id = validate_client_message_id(body.client_message_id)
        text = normalize_text(body.text)
    except InvalidInboundMessage as exc:
        raise ApiError(422, "invalid_request", str(exc)) from exc

    # tenant/connection/visitor chỉ lấy từ token đã verify, không từ body (thiết kế §6.1).
    event = NormalizedChannelEvent(
        tenant_id=visitor.connection.tenant_id,
        channel_connection_id=visitor.connection.id,
        provider="webchat",
        external_message_id=client_message_id,
        external_customer_id=visitor.claims.visitor_id,
        direction="inbound",
        message_type="text",
        text=text,
        occurred_at=datetime.now(UTC),
        trace_id=request.state.trace_id,
    )
    factory = request.app.state.session_factory
    try:
        result = await ReceiveInboundMessage(lambda: SqlConversationUnitOfWork(factory))(event)
    except TransientDatabaseError as exc:
        raise ApiError(503, "busy", "Please retry", {"Retry-After": "1"}) from exc

    return JSONResponse(
        {
            "message_id": str(result.message_id),
            "sequence": result.sequence,
            "conversation_id": str(result.conversation_id),
        },
        status_code=201 if result.created else 200,
    )


@router.get("/public/webchat/v1/events")
async def visitor_events(request: Request, visitor: CurrentVisitor):
    # Kênh lấy thẳng từ token, không tra DB: đăng ký được trước cả tin đầu tiên (§8.5).
    key = f"{visitor.connection.id}:{visitor.claims.visitor_id}"
    return sse_response(request, f"evt:v:{key}", limit_key=f"v:{key}", limit=STREAMS_PER_VISITOR)


@router.get("/public/webchat/v1/messages")
async def list_messages(
    request: Request,
    visitor: CurrentVisitor,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    empty = {"conversation_id": None, "messages": [], "has_more": False}
    async with request.app.state.session_factory() as s:
        identity = await CustomerRepository(s).get_identity(
            visitor.ctx, visitor.connection.id, visitor.claims.visitor_id
        )
        if identity is None:  # chưa gửi tin nào
            return empty
        conv = await ConversationRepository(s).latest_for_customer(
            visitor.ctx, identity.customer_id, visitor.connection.id
        )
        if conv is None:
            return empty
        rows = await MessageRepository(s).list_after(
            visitor.ctx, conv.id, after_sequence, limit + 1
        )

    return {
        "conversation_id": str(conv.id),
        "messages": [
            {
                "id": str(m.id),
                "sequence": m.sequence,
                "direction": m.direction,
                "sender_type": m.sender_type,
                "text": m.content,
                "occurred_at": m.occurred_at.isoformat(),
                # để widget đối chiếu tin gửi lạc quan; tin của nhân viên không lộ id nội bộ
                "client_message_id": m.external_message_id if m.direction == "inbound" else None,
            }
            for m in rows[:limit]
        ],
        "has_more": len(rows) > limit,
    }
