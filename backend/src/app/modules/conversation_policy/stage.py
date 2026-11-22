"""Chạy tầng chính sách cho một cụm tin đã gom, rồi chuyển cho agent (bước 2 §12.2).

Thứ tự trong worker: chính sách (một transaction: ghi quyết định + đổi `handling`) ⇒ agent runtime.
Agent runtime tự đọc `handling` ở cổng nên quyết định "không chạy agent" luôn để lại một run
`skipped` trong trace. Bước 2.7 chỉ GHI tin tự động (`sent=false`); gửi là việc của `outbound`
ở 2.9.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.agent_runtime.infrastructure.run_requests import RunRequest
from app.modules.conversation_policy.domain.policy import decide
from app.modules.conversation_policy.infrastructure import repository as repo

log = logging.getLogger(__name__)

Handler = Callable[[RunRequest], Awaitable[None]]


class PolicyStage:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        next_handler: Handler,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._factory, self._next, self._clock = session_factory, next_handler, clock

    async def handle(self, req: RunRequest) -> None:
        async with self._factory() as s, s.begin():
            await self._apply(s, req)
        await self._next(req)

    async def _apply(self, s: AsyncSession, req: RunRequest) -> None:
        release = await repo.current_release(s, req.tenant_id)
        if release is None:  # shop chưa cấu hình: không có gì để quyết định
            return
        conv = await repo.lock_conversation(s, req.tenant_id, req.conversation_id)
        if conv is None or conv.status != "open":
            return
        # Worker retry sau khi chính sách đã commit: không quyết định (và không đổi handling) lần 2.
        if await repo.decision_exists(s, req.tenant_id, req.conversation_id, req.to_sequence):
            return
        state = await repo.load_state(
            s, req.tenant_id, req.conversation_id, conv.handling, req.from_sequence
        )
        decision = decide(release.config, state, self._clock(), shop_name=conv.shop_name)
        await repo.record(
            s,
            tenant_id=req.tenant_id,
            conversation_id=req.conversation_id,
            to_sequence=req.to_sequence,
            release_id=release.id,
            handling_before=conv.handling,
            decision=decision,
        )
        log.info(
            "policy decision",
            extra={
                "conversation_id": str(req.conversation_id),
                "situation": decision.situation,
                "run_agent": decision.run_agent,
            },
        )
