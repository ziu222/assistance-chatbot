"""Lỗi chung `{"error": {"code", "message"}, "trace_id"}` (thiết kế §7). Không trả stack trace."""

import logging
import re
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.shared.infrastructure.logging import trace_id_var
from app.shared.infrastructure.metrics import HTTP_DURATION

access_log = logging.getLogger("app.access")
# Trace từ client chỉ nhận khi đúng dạng (chặn chèn dòng log / header).
_TRACE_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")

MAX_PUBLIC_BODY_BYTES = 16 * 1024
# Endpoint ai cũng gọi được: chặn body trước khi đọc.
UNAUTHENTICATED_POSTS = ("/public/", "/api/v1/auth/login")


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        self.status, self.code, self.message, self.headers = status, code, message, headers


def error_response(
    request: Request, status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None) or uuid4().hex
    return JSONResponse(
        {"error": {"code": code, "message": message}, "trace_id": trace_id},
        status_code=status,
        headers={**(headers or {}), "X-Trace-Id": trace_id},
    )


def _guard_body(request: Request) -> JSONResponse | None:
    # Chặn trước khi đọc body: Starlette sẽ đọc cả body vào bộ nhớ nếu để tới route.
    if request.method != "POST" or not request.url.path.startswith(UNAUTHENTICATED_POSTS):
        return None
    ctype = request.headers.get("content-type", "")
    length = request.headers.get("content-length")
    if not ctype.lower().startswith("application/json"):
        return error_response(request, 415, "unsupported_media_type", "JSON required")
    if length is None or not length.isdigit():
        return error_response(request, 411, "length_required", "Content-Length required")
    if int(length) > MAX_PUBLIC_BODY_BYTES:
        return error_response(request, 413, "payload_too_large", "Body too large")
    return None


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def trace_measure_guard(request: Request, call_next):
        incoming = request.headers.get("x-trace-id", "")
        trace_id = incoming if _TRACE_ID.fullmatch(incoming) else uuid4().hex
        request.state.trace_id = trace_id
        ctx_token = trace_id_var.set(trace_id)
        started = time.perf_counter()
        status = 500
        try:
            response = _guard_body(request) or await call_next(request)
            status = response.status_code
            response.headers.setdefault("X-Trace-Id", trace_id)
            return response
        finally:
            # Nhãn là template của route (vd /w/{public_key}), không phải đường dẫn thật.
            route = getattr(request.scope.get("route"), "path", None) or "unmatched"
            elapsed = time.perf_counter() - started
            HTTP_DURATION.labels(route=route, method=request.method).observe(elapsed)
            if route != "/metrics":
                # Không IP, không query string, không body.
                access_log.info(
                    "request",
                    extra={
                        "trace_id": trace_id,
                        "route": route,
                        "method": request.method,
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 1),
                    },
                )
            trace_id_var.reset(ctx_token)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(request, exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        return error_response(
            request, exc.status_code, f"http_{exc.status_code}", str(exc.detail), exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        # Không vọng lại giá trị đầu vào (có thể chứa nội dung tin của khách).
        fields = sorted({".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()})
        return error_response(
            request, 422, "invalid_request", f"Invalid fields: {', '.join(fields)}"
        )
