"""Chạy tầng chính sách cho một cụm tin đã gom, rồi chuyển cho agent (bước 2 §12.2).

Thứ tự trong worker: chính sách (một transaction: ghi quyết định + đổi `handling`) ⇒ agent runtime.
Agent runtime tự đọc `handling` ở cổng nên quyết định "không chạy agent" luôn để lại một run
`skipped` trong trace. Tin tự động đi qua `SendGate` của module `outbound` (2.9): shop chưa
bật công tắc gửi thì chỉ được ghi lại (`sent=false`, kèm lý do).
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.agent_runtime.infrastructure.run_requests import RunRequest
from app.modules.conversation_policy.domain.escalation import ticket_priority
from app.modules.conversation_policy.domain.policy import (
    PolicyConfig,
    decide,
    detect_handoff,
    render_handoff_ack,
)
from app.modules.conversation_policy.infrastructure import repository as repo
from app.modules.conversation_policy.infrastructure.handoffs import bump_priority, open_handoff
from app.modules.outbound.canned import GateInput, send_block, send_canned

log = logging.getLogger(__name__)

Handler = Callable[[RunRequest], Awaitable[None]]


async def send_handoff_ack_if_enabled(
    s: AsyncSession,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    handoff_id: UUID,
    conv: repo.ConversationForPolicy,
    release: repo.Release | None,
    now: datetime,
) -> bool:
    """Gửi tin tự động `handoff_ack` qua SendGate ngay khi vừa mở ticket (idempotent theo ticket).

    Người gọi PHẢI đang giữ khóa `FOR UPDATE` trên hội thoại (qua `repo.lock_conversation`).
    """
    if release is None:
        return False
    text = render_handoff_ack(release.config, now, shop_name=conv.shop_name)
    if text is None:
        return False
    block = send_block(
        GateInput(
            release.auto_send,
            conv.provider,
            conv.status,
            "waiting_human",
            allow_waiting_human=True,
        )
    )
    if block is not None:
        return False
    return await send_canned(
        s,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        to_sequence=0,
        situation="handoff_ack",
        release_id=release.id,
        content=text,
        external_message_id=f"handoff_ack:{handoff_id}",
    )


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
        now = self._clock()
        if handling == "bot":
            texts = await repo.customer_texts(
                s, req.tenant_id, req.conversation_id, req.from_sequence, req.to_sequence
            )
            match = detect_handoff(config, texts)
            if match is not None:
                esc_score = await repo.latest_escalation_score(
                    s, req.tenant_id, req.conversation_id
                )
                prio = ticket_priority(match[0], esc_score, config.escalation_sensitivity)
                handoff_id, created = await open_handoff(
                    s,
                    req.tenant_id,
                    req.conversation_id,
                    source=match[0],
                    reason=match[1],
                    priority=prio,
                )
                handling = "waiting_human"
                if created:
                    await send_handoff_ack_if_enabled(
                        s,
                        tenant_id=req.tenant_id,
                        conversation_id=req.conversation_id,
                        handoff_id=handoff_id,
                        conv=conv,
                        release=release,
                        now=now,
                    )
        elif handling == "waiting_human":
            esc_score = await repo.latest_escalation_score(s, req.tenant_id, req.conversation_id)
            prio = ticket_priority("topic", esc_score, config.escalation_sensitivity)
            if prio > 0:
                await bump_priority(s, req.tenant_id, req.conversation_id, prio)
        if release is None:  # shop chưa cấu hình tin tự động
            return
        # Worker retry sau khi chính sách đã commit: không quyết định (và không đổi handling) lần 2.
        if await repo.decision_exists(s, req.tenant_id, req.conversation_id, req.to_sequence):
            return
        state = await repo.load_state(
            s, req.tenant_id, req.conversation_id, handling, req.from_sequence
        )
        decision = decide(config, state, now, shop_name=conv.shop_name)
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
            if (
                block is None
                and decision.run_agent
                and decision.situation in ("paused", "after_hours")
                and await repo.autopilot_active(s, req.tenant_id, req.conversation_id)
            ):
                # "Shop tạm nghỉ / mở lại lúc…" rồi bot trả lời ngay nghe mâu thuẫn. Hoãn câu này:
                # bot tự trả lời được thì bỏ; không trả lời thì AutopilotStage gửi nó thay.
                block = repo.DEFERRED
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
