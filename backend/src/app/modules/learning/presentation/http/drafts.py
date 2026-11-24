"""REST nháp ở chế độ Learning (thiết kế 05 §4): xem nháp, bỏ nháp. Gửi nháp = API trả lời."""

from uuid import UUID

from fastapi import APIRouter, Request

from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import Csrf, CurrentStaff
from app.modules.learning.infrastructure.drafts import current_draft, record_use
from app.shared.presentation.http_errors import ApiError

router = APIRouter(prefix="/api/v1/conversations")


def _require(actor: StaffActor, action: str) -> UUID:
    if not authorize(actor, action, actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    return actor.tenant_id


@router.get("/{conversation_id}/draft")
async def get_draft(conversation_id: UUID, request: Request, actor: CurrentStaff) -> dict:
    """Nháp hợp lệ mới nhất, chỉ khi agent của kênh ở chế độ Learning; không có thì `null`."""
    tenant_id = _require(actor, "conversation.read")
    async with request.app.state.session_factory() as s, s.begin():
        return {"draft": await current_draft(s, tenant_id, conversation_id)}


@router.post("/{conversation_id}/draft/{draft_id}/discard", dependencies=[Csrf])
async def discard(
    conversation_id: UUID, draft_id: UUID, request: Request, actor: CurrentStaff
) -> dict:
    tenant_id = _require(actor, "conversation.reply")
    async with request.app.state.session_factory() as s, s.begin():
        action = await record_use(
            s, tenant_id, conversation_id, draft_id, message_id=None, sent=None, used=False,
            user_id=actor.user_id,
        )  # fmt: skip
    if action is None:
        raise ApiError(404, "not_found", "Draft not found or already handled")
    return {"action": action}
