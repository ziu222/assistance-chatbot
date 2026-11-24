"""Adapter OpenAI (Chat Completions). Dịch định dạng trung lập qua lại (ADR-0015).

Khác biệt cần dịch:
- system_blocks → MỘT message `system` (OpenAI cache tự động theo tiền tố, không cần điểm cache).
- Tool strict của OpenAI bắt MỌI thuộc tính nằm trong `required`: thuộc tính tùy chọn được gửi đi
  dưới dạng nullable, và khi nhận về giá trị `null` của chúng bị bỏ, để runtime không phải biết.
- Kết quả tool là từng message `role=tool`; arguments của tool call là chuỗi JSON.
- Token vào của OpenAI đã GỒM phần cache ⇒ chuẩn hóa: input = prompt_tokens − cached_tokens.
- OpenAI không có `effort` kiểu Anthropic: routes.py chặn effort cho đích OpenAI, không ngầm bỏ.
"""

import copy
import json

import httpx2 as httpx
import openai
from openai.lib._pydantic import to_strict_json_schema
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
    ToolSpec,
    Turn,
    Usage,
)

_FINISH: dict[str, StopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


def strict_schema(schema: dict) -> dict:
    """Dạng strict của OpenAI: mọi thuộc tính bắt buộc; thuộc tính tùy chọn cho phép null."""
    out = copy.deepcopy(schema)
    props = out.get("properties")
    if out.get("type") == "object" and props is not None:
        required = set(out.get("required", []))
        out["properties"] = {
            name: sub if name in required else {"anyOf": [sub, {"type": "null"}]}
            for name, sub in ((n, strict_schema(p)) for n, p in props.items())
        }
        out["required"] = list(props)
        out["additionalProperties"] = False
    return out


def _messages(req: LLMRequest) -> list[dict]:
    out: list[dict] = []
    if req.system_blocks:
        out.append({"role": "system", "content": "\n\n".join(req.system_blocks)})
    t: Turn
    for t in req.turns:
        if t.role == "user":
            out.append({"role": "user", "content": t.text or ""})
        elif t.role == "assistant":
            msg: dict = {"role": "assistant", "content": t.text}
            if t.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {
                            "name": c.name,
                            "arguments": json.dumps(c.arguments, ensure_ascii=False),
                        },
                    }
                    for c in t.tool_calls
                ]
            out.append(msg)
        else:
            out += [
                {"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}
                for r in t.tool_results
            ]
    return out


def build_params(target: Target, req: LLMRequest) -> dict:
    params: dict = {
        "model": target.model,
        "max_completion_tokens": target.max_tokens,
        "messages": _messages(req),
    }
    if req.tools:
        params["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": strict_schema(t.input_schema),
                    "strict": True,
                },
            }
            for t in req.tools
        ]
    if req.output_schema is not None:
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": req.output_schema.__name__,
                "schema": to_strict_json_schema(req.output_schema),
                "strict": True,
            },
        }
    return params


def retry_after(exc: Exception) -> float | None:
    """Giây chờ theo header Retry-After (dạng số giây); không có hoặc sai dạng ⇒ None."""
    try:
        return float(exc.response.headers.get("retry-after"))  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        return None


def classify_error(exc: Exception) -> Exception:
    provider = OpenAIBackend.provider
    if isinstance(exc, openai.APITimeoutError):
        return ProviderUnavailable(provider, "timeout")
    if isinstance(exc, openai.APIConnectionError):
        return ProviderUnavailable(provider, "connection")
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        if status == 429 and getattr(exc, "code", None) == "insufficient_quota":
            return ProviderUnavailable(provider, "quota", status)  # hết tiền: không tự hết
        if status in (401, 403):
            return ProviderUnavailable(provider, "auth", status)
        if status == 402:
            return ProviderUnavailable(provider, "quota", status)
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
        return BadRequest(f"openai rejected the request (HTTP {status})")
    if isinstance(exc, openai.APIError):
        return ProviderUnavailable(provider, "server_error")
    return exc


def _tool_calls(message, tools: tuple[ToolSpec, ...]) -> tuple[ToolCall, ...]:
    optional = {
        t.name: set(t.input_schema.get("properties", {})) - set(t.input_schema.get("required", []))
        for t in tools
    }
    calls = []
    for tc in message.tool_calls or []:
        if tc.type != "function":
            continue
        try:
            args = json.loads(tc.function.arguments)
        except json.JSONDecodeError as exc:
            raise InvalidOutput("tool arguments are not valid JSON") from exc
        if not isinstance(args, dict):
            raise InvalidOutput("tool arguments are not an object")
        skip = optional.get(tc.function.name, set())
        args = {k: v for k, v in args.items() if not (v is None and k in skip)}
        calls.append(ToolCall(tc.id, tc.function.name, args))
    return tuple(calls)


def parse_completion(completion, target: Target, req: LLMRequest) -> LLMResult:
    choice = completion.choices[0]
    msg = choice.message
    stop: StopReason | None = "refusal" if msg.refusal else _FINISH.get(choice.finish_reason or "")
    if stop is None:
        raise InvalidOutput(f"unexpected finish_reason {choice.finish_reason!r}")
    calls = _tool_calls(msg, req.tools)
    if stop == "tool_use" and not calls:
        raise InvalidOutput("finish_reason tool_calls without tool calls")
    text = msg.content or None
    parsed = None
    if req.output_schema is not None and stop == "end_turn":
        try:
            parsed = req.output_schema.model_validate_json(text or "")
        except ValidationError as exc:
            raise InvalidOutput("output does not match the schema") from exc
    u = completion.usage
    cached = (u.prompt_tokens_details.cached_tokens or 0) if u and u.prompt_tokens_details else 0
    usage = Usage(
        input=max((u.prompt_tokens if u else 0) - cached, 0),
        cache_read=cached,
        cache_write=0,
        output=u.completion_tokens if u else 0,
    )
    return LLMResult(stop, text, parsed, calls, usage, target)


class OpenAIBackend:
    provider = "openai"

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=api_key, base_url=base_url or None, max_retries=0, http_client=http_client
        )

    async def complete(self, target: Target, req: LLMRequest, timeout: float) -> LLMResult:
        try:
            completion = await self._client.chat.completions.create(
                **build_params(target, req), timeout=timeout
            )
        except openai.APIError as exc:
            raise classify_error(exc) from None  # không kèm exc: tránh lộ header/key trong log
        return parse_completion(completion, target, req)
