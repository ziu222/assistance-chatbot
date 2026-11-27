"""Worker tóm tắt hội thoại 3 dòng cho ticket "Nhờ nhân viên" (thiết kế bước 2 §12.5).

Chạy nền trong `agent_worker` (nơi duy nhất giữ key LLM). Luôn gọi LLM ngoài DB transaction; lỗi
LLM hay vượt trần chi phí chỉ đánh dấu `summary_status = 'failed'`, không bao giờ chặn mở ticket.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.agent_runtime.infrastructure.usage import (
    HANDOFF_SUMMARY,
    record_usage,
    spent_today_micro,
)
from app.modules.agent_runtime.llm.port import (
    AllTargetsFailed,
    BadRequest,
    DeadlineExceeded,
    InvalidOutput,
    LLMPort,
    LLMRequest,
    ProviderUnavailable,
    Turn,
)
from app.modules.agent_runtime.llm.pricing import UnknownPrice
from app.modules.conversation_policy.domain.summary import (
    HandoffSummaryOut,
    format_summary,
    system_blocks,
    user_turn,
)
from app.modules.conversation_policy.infrastructure.handoffs import _outbox

log = logging.getLogger(__name__)
ROUTE = "classify"
DEADLINE_S = 20
MAX_MESSAGES = 12
MAX_MESSAGE_CHARS = 500
MIN_CUSTOMER_MESSAGES = 2


@dataclass(frozen=True)
class PendingSummary:
    handoff_id: UUID
    tenant_id: UUID
    conversation_id: UUID
    lines: list[tuple[str, str]]
    customer_count: int


async def _next_pending(s: AsyncSession) -> PendingSummary | None:
    row = (
        await s.execute(
            text(
                "SELECT id, tenant_id, conversation_id FROM handoff_requests "
                "WHERE status IN ('open', 'claimed') AND summary_status = 'pending' "
                "ORDER BY priority DESC, opened_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
        )
    ).first()
    if row is None:
        return None
    handoff_id, tenant_id, conversation_id = row
    msg_rows = (
        await s.execute(
            text(
                "SELECT sender_type, content FROM messages "
                "WHERE tenant_id = :t AND conversation_id = :c "
                "  AND content IS NOT NULL AND content <> '' "
                "ORDER BY sequence DESC LIMIT :n"
            ),
            {"t": tenant_id, "c": conversation_id, "n": MAX_MESSAGES},
        )
    ).all()
    ordered = list(reversed(msg_rows))
    lines = [
        ("khach" if sender == "customer" else "nhan_vien", content[:MAX_MESSAGE_CHARS])
        for sender, content in ordered
    ]
    customer_count = sum(1 for sender, _ in ordered if sender == "customer")
    return PendingSummary(handoff_id, tenant_id, conversation_id, lines, customer_count)


async def _save_summary(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    handoff_id: UUID,
    summary: str | None,
    status: str,
) -> None:
    updated = (
        await s.execute(
            text(
                "UPDATE handoff_requests SET summary = :sum, summary_status = :st "
                "WHERE tenant_id = :t AND id = :id AND summary_status = 'pending' RETURNING id"
            ),
            {"sum": summary, "st": status, "t": tenant_id, "id": handoff_id},
        )
    ).first()
    if updated is not None and status == "done":
        await _outbox(s, tenant_id, conversation_id, "handoff.updated", handoff_id)


class HandoffSummaryWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMPort,
        *,
        daily_cap_micro: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory, self._llm, self._cap, self._clock = (
            session_factory,
            llm,
            daily_cap_micro,
            clock,
        )

    async def process_once(self) -> bool:
        """True nếu đã xử lý một ticket (để vòng lặp xử lý tiếp ngay)."""
        async with self._factory() as s, s.begin():
            pending = await _next_pending(s)
            if pending is None:
                return False
            if pending.customer_count < MIN_CUSTOMER_MESSAGES:
                await _save_summary(
                    s,
                    tenant_id=pending.tenant_id,
                    conversation_id=pending.conversation_id,
                    handoff_id=pending.handoff_id,
                    summary=None,
                    status="skipped",
                )
                return True
            spent = await spent_today_micro(s, pending.tenant_id, purpose=HANDOFF_SUMMARY)
            if spent >= self._cap:
                await _save_summary(
                    s,
                    tenant_id=pending.tenant_id,
                    conversation_id=pending.conversation_id,
                    handoff_id=pending.handoff_id,
                    summary=None,
                    status="failed",
                )
                return True

        try:
            result = await self._llm.complete(
                LLMRequest(
                    route=ROUTE,
                    system_blocks=system_blocks(),
                    turns=(Turn(role="user", text=user_turn(pending.lines)),),
                    output_schema=HandoffSummaryOut,
                    deadline=self._clock() + DEADLINE_S,
                )
            )
        except (AllTargetsFailed, ProviderUnavailable, DeadlineExceeded, BadRequest, InvalidOutput):
            await self._mark_failed(pending)
            return True

        if result.stop_reason == "refusal" or not isinstance(result.parsed, HandoffSummaryOut):
            await self._mark_failed(pending)
            return True

        summary_text = format_summary(result.parsed)
        if not summary_text:
            await self._mark_failed(pending)
            return True

        async with self._factory() as s, s.begin():
            try:
                await record_usage(
                    s,
                    tenant_id=pending.tenant_id,
                    run_id=None,
                    route=HANDOFF_SUMMARY,
                    result=result,
                )
            except UnknownPrice:
                log.error(
                    "handoff summary model has no price", extra={"model": result.served_by.model}
                )
            await _save_summary(
                s,
                tenant_id=pending.tenant_id,
                conversation_id=pending.conversation_id,
                handoff_id=pending.handoff_id,
                summary=summary_text,
                status="done",
            )
        return True

    async def _mark_failed(self, pending: PendingSummary) -> None:
        async with self._factory() as s, s.begin():
            await _save_summary(
                s,
                tenant_id=pending.tenant_id,
                conversation_id=pending.conversation_id,
                handoff_id=pending.handoff_id,
                summary=None,
                status="failed",
            )

    async def run(self, stop: asyncio.Event, poll_s: float = 2.0) -> None:
        while not stop.is_set():
            try:
                busy = await self.process_once()
            except Exception:
                log.exception("handoff summary worker iteration failed")
                busy = False
            if not busy:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_s)
                except TimeoutError:
                    pass
