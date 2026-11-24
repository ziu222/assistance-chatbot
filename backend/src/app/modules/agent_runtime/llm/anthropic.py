"""Adapter Anthropic (Messages API). Gateway hay API chính thức chỉ khác `base_url` + key.

Quy ước dịch (ADR-0015):
- system_blocks → `system` dạng danh sách khối; điểm cache đặt ở khối CUỐI (khối ổn định nhất
  nằm trước, và `tools` được render trước `system` nên cũng nằm trong phần được cache).
- Không gửi `thinking` (Sonnet 5 tự chạy adaptive), không gửi temperature/top_p/top_k.
- effort + structured output cùng nằm trong `output_config`.
- Thinking block KHÔNG được lưu hay phát lại: chúng chỉ hợp lệ với đúng model đã sinh ra
  (ADR-0030), và lịch sử trung lập không chứa chúng.
- SDK `max_retries=0`: retry/failover do FailoverLLM quyết định ở một chỗ duy nhất.
"""

import anthropic
import httpx2 as httpx
from pydantic import ValidationError

from app.modules.agent_runtime.llm.port import (
    BadRequest,
    InvalidOutput,
    LLMRequest,
    LLMResult,
    ProviderUnavailable,
    StopReason,
    Target,
    ToolCall,
    Turn,
    Usage,
)

_STOP: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


def _messages(turns: tuple[Turn, ...]) -> list[dict]:
    out: list[dict] = []
    for t in turns:
        if t.role == "user":
            out.append({"role": "user", "content": t.text or ""})
        elif t.role == "assistant":
            blocks: list[dict] = []
            if t.text:
                blocks.append({"type": "text", "text": t.text})
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in t.tool_calls
            ]
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r.tool_call_id,
                            "content": r.content,
                            **({"is_error": True} if r.is_error else {}),
                        }
                        for r in t.tool_results
                    ],
                }
            )
    return out


def build_params(target: Target, req: LLMRequest) -> dict:
    params: dict = {
        "model": target.model,
        "max_tokens": target.max_tokens,
        "messages": _messages(req.turns),
    }
    if req.system_blocks:
        system = [{"type": "text", "text": b} for b in req.system_blocks]
        system[-1]["cache_control"] = {"type": "ephemeral"}
        params["system"] = system
    if req.tools:
        params["tools"] = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
                "strict": True,
            }
            for t in req.tools
        ]
    output_config: dict = {}
    if target.effort:
        output_config["effort"] = target.effort
    if req.output_schema is not None:
        output_config["format"] = {
            "type": "json_schema",
            "schema": anthropic.transform_schema(req.output_schema),
        }
    if output_config:
        params["output_config"] = output_config
    return params


def retry_after(exc: Exception) -> float | None:
    """Giây chờ theo header Retry-After (dạng số giây); không có hoặc sai dạng ⇒ None."""
    try:
        return float(exc.response.headers.get("retry-after"))  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        return None


def classify_error(exc: Exception) -> Exception:
    """Dịch lỗi SDK sang lỗi trung lập. Theo mã HTTP: ổn định và gateway cũng tuân theo."""
    provider = AnthropicBackend.provider
    if isinstance(exc, anthropic.APITimeoutError):
        return ProviderUnavailable(provider, "timeout")
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderUnavailable(provider, "connection")
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        if status in (401, 403):
            return ProviderUnavailable(provider, "auth", status)
        if status == 402 or (status == 400 and "credit balance" in str(exc).lower()):
            return ProviderUnavailable(provider, "quota", status)  # hết tiền: không tự hết
        if status == 404:
            return ProviderUnavailable(provider, "config", status)  # model không tồn tại
        if status == 408:
            return ProviderUnavailable(provider, "timeout", status)
        if status == 429:
            return ProviderUnavailable(provider, "rate_limited", status, retry_after(exc))
        if status in (503, 529):
            return ProviderUnavailable(provider, "overloaded", status)
        if status >= 500:
            return ProviderUnavailable(provider, "server_error", status)
        return BadRequest(f"anthropic rejected the request (HTTP {status})")
    if isinstance(exc, anthropic.APIError):  # vd phản hồi sai định dạng
        return ProviderUnavailable(provider, "server_error")
    return exc


def parse_message(msg: anthropic.types.Message, target: Target, req: LLMRequest) -> LLMResult:
    stop = _STOP.get(msg.stop_reason or "")
    if stop is None:
        raise InvalidOutput(f"unexpected stop_reason {msg.stop_reason!r}")
    text = "".join(b.text for b in msg.content if b.type == "text") or None
    calls = tuple(
        ToolCall(b.id, b.name, dict(b.input)) for b in msg.content if b.type == "tool_use"
    )
    parsed = None
    if req.output_schema is not None and stop == "end_turn":
        try:
            parsed = req.output_schema.model_validate_json(text or "")
        except ValidationError as exc:
            raise InvalidOutput("output does not match the schema") from exc
    u = msg.usage
    usage = Usage(
        input=u.input_tokens or 0,
        cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
        output=u.output_tokens or 0,
    )
    return LLMResult(stop, text, parsed, calls, usage, target)


class AnthropicBackend:
    provider = "anthropic"

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, base_url=base_url or None, max_retries=0, http_client=http_client
        )

    async def complete(self, target: Target, req: LLMRequest, timeout: float) -> LLMResult:
        try:
            msg = await self._client.messages.create(**build_params(target, req), timeout=timeout)
        except anthropic.APIError as exc:
            raise classify_error(exc) from None  # không kèm exc: tránh lộ header/key trong log
        return parse_message(msg, target, req)
