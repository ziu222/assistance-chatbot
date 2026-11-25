"""REST inbox cho nhân viên (thiết kế §7.2). Tenant luôn lấy từ session, không từ tham số.

Conversation của tenant khác trả 404 (không phải 403) để không lộ sự tồn tại.
"""

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.modules.conversation.application.staff_conversation import (
    ConversationClosed,
    ConversationNotFound,
    DuplicateClientMessageId,
    MarkConversationRead,
    SendStaffMessage,
)
from app.modules.conversation.infrastructure.persistence.repository import MessageRepository
from app.modules.conversation.infrastructure.unit_of_work import SqlConversationUnitOfWork
from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import Csrf, CurrentStaff
from app.modules.inbox.infrastructure import queries
from app.modules.integration.domain.normalization import (
    InvalidInboundMessage,
    normalize_text,
    validate_client_message_id,
)
from app.modules.learning.infrastructure.drafts import discard_shown_draft, record_use
from app.modules.outbound.infrastructure.monitoring import record_staff_correction_if_applicable
from app.shared.application.errors import TransientDatabaseError
from app.shared.domain.tenant import TenantContext
from app.shared.presentation.http_errors import ApiError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/conversations")


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Conversation not found")


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_message_id: str = Field(max_length=64)
    text: str = Field(max_length=16_000)
    draft_id: UUID | None = None  # chế độ Learning: nhân viên bấm Dùng/Sửa nháp này (thiết kế 05)


def _ctx(actor: StaffActor, action: str) -> TenantContext:
    if not authorize(actor, action, actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    return TenantContext(actor.tenant_id)


def _uow(request: Request):
    factory = request.app.state.session_factory
    return lambda: SqlConversationUnitOfWork(factory)


@router.get("")
async def list_conversations(
    request: Request,
    actor: CurrentStaff,
    status: Literal["open", "closed"] | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    ctx = _ctx(actor, "conversation.read")
    async with request.app.state.session_factory() as s:
        try:
            rows, next_cursor = await queries.list_conversations(
                s, ctx, status=status, cursor=cursor, limit=limit
            )
        except queries.InvalidCursor as exc:
            raise ApiError(422, "invalid_request", "Invalid cursor") from exc
    return {"items": [queries.conversation_json(r) for r in rows], "next_cursor": next_cursor}


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: UUID, request: Request, actor: CurrentStaff) -> dict:
    ctx = _ctx(actor, "conversation.read")
    async with request.app.state.session_factory() as s:
        row = await queries.get_conversation(s, ctx, conversation_id)
    if row is None:
        raise _not_found()
    return queries.conversation_json(row)


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: UUID,
    request: Request,
    actor: CurrentStaff,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict:
    ctx = _ctx(actor, "conversation.read")
    async with request.app.state.session_factory() as s:
        if await queries.get_conversation(s, ctx, conversation_id) is None:
            raise _not_found()
        rows = await MessageRepository(s).list_after(
            ctx, conversation_id, after_sequence, limit + 1
        )
    return {
        "messages": [
            {
                "id": str(m.id),
                "sequence": m.sequence,
                "direction": m.direction,
                "sender_type": m.sender_type,
                "sender_ref": m.sender_ref,
                "text": m.content,
                "status": m.status,
                "occurred_at": m.occurred_at.isoformat(),
                "client_message_id": m.external_message_id,
            }
            for m in rows[:limit]
        ],
        "has_more": len(rows) > limit,
    }


@router.post("/{conversation_id}/messages", dependencies=[Csrf])
async def reply(
    conversation_id: UUID, body: ReplyIn, request: Request, actor: CurrentStaff
) -> JSONResponse:
    ctx = _ctx(actor, "conversation.reply")
    try:
        client_message_id = validate_client_message_id(body.client_message_id)
        text = normalize_text(body.text)
    except InvalidInboundMessage as exc:
        raise ApiError(422, "invalid_request", str(exc)) from exc
    try:
        result = await SendStaffMessage(_uow(request))(
            ctx,
            conversation_id,
            user_id=actor.user_id,
            client_message_id=client_message_id,
            text=text,
            trace_id=request.state.trace_id,
        )
    except ConversationNotFound as exc:
        raise _not_found() from exc
    except ConversationClosed as exc:
        raise ApiError(409, "conversation_closed", "Conversation is closed") from exc
    except DuplicateClientMessageId as exc:
        raise ApiError(409, "duplicate_client_message_id", "client_message_id reused") from exc
    except TransientDatabaseError as exc:
        raise ApiError(503, "busy", "Please retry", {"Retry-After": "1"}) from exc
    draft_action = None
    if result.created:
        # Không chung transaction với tin nhắn (ponytail ở learning.infrastructure.drafts): lỗi ở
        # đây chỉ làm mất một bản ghi học, không ảnh hưởng tin đã gửi.
        try:
            async with request.app.state.session_factory() as s, s.begin():
                if body.draft_id:
                    draft_action = await record_use(
                        s, ctx.tenant_id, conversation_id, body.draft_id,
                        message_id=result.message_id, sent=text, used=True, user_id=actor.user_id,
                    )  # fmt: skip
                else:
                    draft_action = await discard_shown_draft(
                        s, ctx.tenant_id, conversation_id, result.message_id, result.sequence,
                        actor.user_id,
                    )  # fmt: skip
                await record_staff_correction_if_applicable(
                    s,
                    ctx.tenant_id,
                    conversation_id,
                    staff_sequence=result.sequence,
                    staff_text=text,
                )
        except Exception:
            log.exception("could not record draft use")
    return JSONResponse(
        {
            "message_id": str(result.message_id),
            "sequence": result.sequence,
            "conversation_id": str(result.conversation_id),
            "draft_action": draft_action,
        },
        status_code=201 if result.created else 200,
    )


@router.post("/{conversation_id}/read", status_code=204, dependencies=[Csrf])
async def mark_read(conversation_id: UUID, request: Request, actor: CurrentStaff) -> None:
    ctx = _ctx(actor, "conversation.reply")
    try:
        await MarkConversationRead(_uow(request))(
            ctx, conversation_id, trace_id=request.state.trace_id
        )
    except ConversationNotFound as exc:
        raise _not_found() from exc
    except TransientDatabaseError as exc:
        raise ApiError(503, "busy", "Please retry", {"Retry-After": "1"}) from exc
