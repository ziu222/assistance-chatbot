from fastapi import Request
from fastapi.responses import StreamingResponse

from app.shared.infrastructure.sse_hub import TooManyStreams, sse_frames
from app.shared.presentation.http_errors import ApiError


def sse_response(
    request: Request, channel: str, *, limit_key: str, limit: int
) -> StreamingResponse:
    hub = request.app.state.signal_hub
    try:
        sub = hub.open(channel, limit_key=limit_key, limit=limit)
    except TooManyStreams as exc:
        raise ApiError(
            429, "too_many_streams", "Too many open streams", {"Retry-After": "5"}
        ) from exc
    return StreamingResponse(
        sse_frames(hub, sub),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},  # proxy không buffer
    )
