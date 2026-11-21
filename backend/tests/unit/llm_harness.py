"""Harness cho contract test adapter: mỗi provider một lớp, cùng giao diện.

HTTP được giả bằng httpx.MockTransport: không mạng, nhưng SDK thật chạy trọn vẹn (dựng request,
parse response, ném lỗi) nên bắt được lỗi dịch định dạng mà mock ở mức hàm không thấy.
"""

import json
from collections.abc import Callable

import httpx2 as httpx

from app.modules.agent_runtime.llm.anthropic import AnthropicBackend
from app.modules.agent_runtime.llm.port import Backend, Target

Handler = Callable[[httpx.Request], httpx.Response]


def body_of(request: httpx.Request) -> dict:
    return json.loads(request.content)


def flat_text(request: httpx.Request) -> str:
    """Toàn bộ nội dung đã gửi, dạng chữ thường (không escape) để dò xem tri thức có tới nơi."""
    return json.dumps(body_of(request), ensure_ascii=False)


def json_response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(status, json=payload)


class AnthropicHarness:
    name = "anthropic"
    target = Target("anthropic", "claude-sonnet-5", max_tokens=1024, effort="medium")

    def backend(self, handler: Handler) -> Backend:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return AnthropicBackend("test-key", base_url="https://llm.test", http_client=client)

    def _message(self, content: list[dict], stop: str, usage: dict) -> httpx.Response:
        return json_response(
            200,
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": self.target.model,
                "content": content,
                "stop_reason": stop,
                "stop_sequence": None,
                "usage": usage,
            },
        )

    def text(self, text: str, *, input=10, cache_read=0, cache_write=0, output=5, stop="end_turn"):
        return self._message(
            [{"type": "text", "text": text}],
            stop,
            {
                "input_tokens": input,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
                "output_tokens": output,
            },
        )

    def tool_call(self, call_id: str, name: str, args: dict) -> httpx.Response:
        return self._message(
            [{"type": "tool_use", "id": call_id, "name": name, "input": args}],
            "tool_use",
            {"input_tokens": 10, "output_tokens": 5},
        )

    def refusal(self) -> httpx.Response:
        return self._message([], "refusal", {"input_tokens": 10, "output_tokens": 0})

    def error(self, status: int, message: str = "boom") -> httpx.Response:
        kind = {429: "rate_limit_error", 401: "authentication_error", 404: "not_found_error"}.get(
            status, "api_error" if status >= 500 else "invalid_request_error"
        )
        return json_response(status, {"type": "error", "error": {"type": kind, "message": message}})

    def wire_tools(self, request: httpx.Request) -> list[tuple[str, bool, dict]]:
        return [
            (t["name"], t.get("strict", False), t["input_schema"])
            for t in body_of(request).get("tools", [])
        ]


HARNESSES = [AnthropicHarness()]
