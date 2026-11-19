from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.modules.identity.domain.authorization import authorize
from app.modules.identity.presentation.http.auth import CurrentStaff
from app.shared.presentation.http_errors import ApiError
from app.shared.presentation.sse import sse_response

STREAMS_PER_TENANT = 20  # thiết kế §8.3

router = APIRouter()


@router.get("/api/v1/events")
async def staff_events(request: Request, actor: CurrentStaff) -> StreamingResponse:
    if not authorize(actor, "conversation.read", actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    # Kênh lấy từ session, không từ tham số.
    return sse_response(
        request,
        f"evt:t:{actor.tenant_id}",
        limit_key=f"t:{actor.tenant_id}",
        limit=STREAMS_PER_TENANT,
    )
