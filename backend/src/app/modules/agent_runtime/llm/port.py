"""Cổng LLM trung lập (ADR-0015, ADR-0030).

Runtime chỉ làm việc với các kiểu dữ liệu ở đây. Mỗi provider có một adapter dịch qua lại
định dạng riêng của họ; lịch sử một run lưu ở dạng trung lập nên phát lại sang provider khác
được bất cứ lúc nào. Không có trường nào của riêng một provider ở file này.
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel

StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal"]
FailureReason = Literal[
    "timeout", "connection", "server_error", "overloaded", "rate_limited", "auth", "config", "quota"
]
# Lỗi không tự hết: mở circuit lâu thay vì chờ vài giây.
PERSISTENT_FAILURES: frozenset[str] = frozenset({"auth", "config", "quota"})


@dataclass(frozen=True)
class Target:
    """Một đích trong route: provider + model + thông số của riêng đích đó."""

    provider: str
    model: str
    max_tokens: int
    effort: str | None = None
    timeout_s: float = 30.0

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON schema strict; tenant không bao giờ nằm trong này


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Turn:
    """Một lượt hội thoại. `user`: text. `assistant`: text và/hoặc tool_calls.
    `tool_results`: kết quả backend trả cho các tool_calls của lượt assistant ngay trước."""

    role: Literal["user", "assistant", "tool_results"]
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()


@dataclass(frozen=True)
class Usage:
    """Chuẩn hóa: `input` là token vào KHÔNG nằm trong cache (provider nào cũng quy về đây)."""

    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0


@dataclass(frozen=True)
class LLMRequest:
    route: str
    system_blocks: tuple[str, ...]  # ổn định → thay đổi; adapter tự đặt điểm cache
    turns: tuple[Turn, ...]
    tools: tuple[ToolSpec, ...] = ()
    output_schema: type[BaseModel] | None = None
    deadline: float | None = None  # time.monotonic(): hết ngân sách thời gian của cả run


@dataclass(frozen=True)
class FailedAttempt:
    target: Target
    reason: str


@dataclass(frozen=True)
class LLMResult:
    stop_reason: StopReason
    text: str | None
    parsed: BaseModel | None
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    served_by: Target
    failover_from: tuple[FailedAttempt, ...] = ()


class LLMPort(Protocol):
    async def complete(self, req: LLMRequest) -> LLMResult: ...


class Backend(Protocol):
    """Adapter của một provider: gọi MỘT đích, không retry, không failover."""

    provider: str

    async def complete(self, target: Target, req: LLMRequest, timeout: float) -> LLMResult: ...


class LLMError(Exception):
    pass


class ProviderUnavailable(LLMError):
    """Provider có vấn đề (không phải lỗi của yêu cầu) ⇒ failover được."""

    def __init__(
        self,
        provider: str,
        reason: FailureReason,
        status: int | None = None,
        retry_after: float | None = None,  # giây, từ header Retry-After (429) nếu có
    ) -> None:
        super().__init__(f"{provider}: {reason}")  # không chứa nội dung prompt hay key
        self.provider, self.reason, self.status = provider, reason, status
        self.retry_after = retry_after


class BadRequest(LLMError):
    """Lỗi do chính yêu cầu (400, vượt context): đích khác cũng sẽ lỗi ⇒ KHÔNG failover."""


class InvalidOutput(LLMError):
    """Model trả output sai schema/định dạng."""


class DeadlineExceeded(LLMError):
    """Ngân sách thời gian của run đã hết trước khi gọi được đích nào."""


class AllTargetsFailed(LLMError):
    def __init__(self, attempts: tuple[FailedAttempt, ...]) -> None:
        detail = ", ".join(f"{a.target.label}={a.reason}" for a in attempts) or "no target"
        super().__init__(f"all targets failed: {detail}")
        self.attempts = attempts
