"""Chạy tầng chính sách cho một cụm tin đã gom, rồi chuyển cho agent (bước 2 §12.2).

Thứ tự trong worker: chính sách (một transaction: ghi quyết định + đổi `handling`) ⇒ agent runtime.
Agent runtime tự đọc `handling` ở cổng nên quyết định "không chạy agent" luôn để lại một run
`skipped` trong trace. Tin tự động đi qua `SendGate` của module `outbound` (2.9): shop chưa
bật công tắc gửi thì chỉ được ghi lại (`sent=false`, kèm lý do).
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.agent_runtime.infrastructure.run_requests import RunRequest
from app.modules.conversation_policy.domain.policy import PolicyConfig, decide, detect_handoff
from app.modules.conversation_policy.infrastructure import repository as repo
from app.modules.conversation_policy.infrastructure.handoffs import open_handoff
from app.modules.outbound.canned import GateInput, send_block, send_canned

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
        conv = await repo.lock_conversation(s, req.tenant_id, req.conversation_id)
        if conv is None or conv.status != "open":
            return
        release = await repo.current_release(s, req.tenant_id)
        config = release.config if release else PolicyConfig()
        handling = conv.handling
        # Khách đòi gặp người (tín hiệu xác định) ⇒ ticket, kể cả khi shop chưa cấu hình gì:
        # khách luôn có lối tới người. Ticket đã có thì không mở thêm (an toàn khi retry).
        if handling == "bot":
            texts = await repo.customer_texts(
                s, req.tenant_id, req.conversation_id, req.from_sequence, req.to_sequence
            )
            match = detect_handoff(config, texts)
            if match is not None:
                await open_handoff(
                    s, req.tenant_id, req.conversation_id, source=match[0], reason=match[1]
                )
                handling = "waiting_human"
        if release is None:  # shop chưa cấu hình tin tự động
            return
        # Worker retry sau khi chính sách đã commit: không quyết định (và không đổi handling) lần 2.
        if await repo.decision_exists(s, req.tenant_id, req.conversation_id, req.to_sequence):
            return
        state = await repo.load_state(
            s, req.tenant_id, req.conversation_id, handling, req.from_sequence
        )
        decision = decide(config, state, self._clock(), shop_name=conv.shop_name)
        await repo.record(
            s,
            tenant_id=req.tenant_id,
            conversation_id=req.conversation_id,
            to_sequence=req.to_sequence,
            release_id=release.id,
            handling_before=handling,
            decision=decision,
        )
        if decision.text is not None:
            block = send_block(GateInput(release.auto_send, conv.provider, conv.status, handling))
            sent = block is None and await send_canned(
                s,
                tenant_id=req.tenant_id,
                conversation_id=req.conversation_id,
                to_sequence=req.to_sequence,
                situation=decision.situation or "",
                release_id=release.id,
                content=decision.text,
            )
            await repo.mark_send(
                s, req.tenant_id, req.conversation_id, req.to_sequence, sent=sent, block=block
            )
        log.info(
            "policy decision",
            extra={
                "conversation_id": str(req.conversation_id),
                "situation": decision.situation,
                "run_agent": decision.run_agent,
            },
        )
