from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class WidgetStatic(StaticFiles):
    """Bundle tên cố định: buộc trình duyệt kiểm tra lại (ETag) để bản mới có hiệu lực ngay."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
