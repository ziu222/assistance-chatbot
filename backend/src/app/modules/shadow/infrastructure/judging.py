"""Giám khảo chạy nền trong agent worker (thiết kế 04 §6, giai đoạn S2).

Chỉ chấm cặp `paired` mà code chưa kết luận. Có trần chi phí riêng mỗi tenant/ngày và lấy mẫu:
chấm hết tới `full_per_day` cặp, quá mức đó thì mỗi ý định chấm thêm tối đa `per_intent_after`.
Lỗi provider ⇒ thử lại ở lượt sau, tối đa 3 lần. Gọi từng cái một và nghỉ giữa các lần, vì gateway
chỉ chịu khoảng 2 request đồng thời và đường soạn nháp cho khách phải được ưu tiên.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.agent_runtime.infrastructure.usage import JUDGE, record_usage, spent_today_micro
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
from app.modules.shadow.domain.judge import (
    JUDGE_PROMPT_VERSION,
    SYSTEM,
    JudgeOut,
    finalize,
    user_turn,
)
from app.shared.infrastructure.metrics import SHADOW_COMPARISONS

log = logging.getLogger(__name__)
DEADLINE_S = 60
MAX_ATTEMPTS = 3
_MIDNIGHT = "date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"

_NEXT = text(
    """
    SELECT c.id, c.tenant_id, c.conversation_id, c.intent, c.code_checks, c.judge_attempts,
           c.reference_from_seq, c.reference_to_seq, r.from_sequence, r.to_sequence, d.text
    FROM shadow_comparisons c
    JOIN agent_runs r ON r.tenant_id = c.tenant_id AND r.id = c.run_id
    JOIN agent_drafts d ON d.tenant_id = c.tenant_id AND d.id = c.draft_id
    WHERE c.status = 'paired' AND c.code_verdict IS NULL AND c.judged_at IS NULL
      AND c.judge_skipped IS NULL AND c.judge_attempts < :max
    ORDER BY c.created_at
    LIMIT 1
    FOR UPDATE OF c SKIP LOCKED
    """
)


async def _texts(s: AsyncSession, c: Any, sender: str, lo: int, hi: int) -> list[str]:
    rows = await s.execute(
        text(
            "SELECT content FROM messages WHERE tenant_id = :t AND conversation_id = :c "
            "AND sender_type = :who AND sequence BETWEEN :lo AND :hi ORDER BY sequence"
        ),
        {"t": c.tenant_id, "c": c.conversation_id, "who": sender, "lo": lo, "hi": hi},
    )
    return [r[0] for r in rows]


class JudgeWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMPort,
        *,
        daily_cap_micro: int,
        full_per_day: int,
        per_intent_after: int,
        pause_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory, self._llm = session_factory, llm
        self._cap, self._full, self._per_intent = daily_cap_micro, full_per_day, per_intent_after
        self._pause, self._clock = pause_s, clock

    async def _skip_reason(self, s: AsyncSession, c: Any) -> str | None:
        if await spent_today_micro(s, c.tenant_id, purpose=JUDGE) >= self._cap:
            return "daily_cap"
        counts = (
            await s.execute(
                text(
                    "SELECT count(*), count(*) FILTER (WHERE intent IS NOT DISTINCT FROM :i) "
                    f"FROM shadow_comparisons WHERE tenant_id = :t AND judged_at >= {_MIDNIGHT}"
                ),
                {"t": c.tenant_id, "i": c.intent},
            )
        ).one()
        if counts[0] >= self._full and counts[1] >= self._per_intent:
            return "sampled_out"
        return None

    async def process_once(self) -> bool:
        """True nếu đã xử lý một cặp (để vòng lặp chạy tiếp)."""
        # ponytail: giữ khóa dòng trong lúc gọi LLM (vài giây) cho đơn giản; đổi sang cột "đang
        # chấm" nếu chạy nhiều agent worker và thấy tranh khóa.
        async with self._factory() as s, s.begin():
            c = (await s.execute(_NEXT, {"max": MAX_ATTEMPTS})).first()
            if c is None:
                return False
            reason = await self._skip_reason(s, c)
            if reason:
                await self._mark(s, c.id, judge_skipped=reason)
                return True
            customer = await _texts(s, c, "customer", c.from_sequence, c.to_sequence)
            staff = await _texts(s, c, "human", c.reference_from_seq, c.reference_to_seq)
            try:
                result = await self._llm.complete(
                    LLMRequest(
                        route=JUDGE,
                        system_blocks=(SYSTEM,),
                        turns=(
                            Turn(
                                role="user",
                                text=user_turn(customer, c.text, "\n".join(staff), c.code_checks),
                            ),
                        ),
                        output_schema=JudgeOut,
                        deadline=self._clock() + DEADLINE_S,
                    )
                )
            except (AllTargetsFailed, ProviderUnavailable, DeadlineExceeded, InvalidOutput):
                attempts = c.judge_attempts + 1
                await self._mark(
                    s,
                    c.id,
                    judge_attempts=attempts,
                    judge_skipped="gave_up" if attempts >= MAX_ATTEMPTS else None,
                )
                return True
            except BadRequest:
                await self._mark(s, c.id, judge_skipped="gave_up")
                return True
            if result.stop_reason == "refusal" or not isinstance(result.parsed, JudgeOut):
                await self._mark(s, c.id, judge_skipped="refusal")
                return True
            try:
                cost = await record_usage(
                    s, tenant_id=c.tenant_id, run_id=None, route=JUDGE, result=result
                )
            except UnknownPrice:  # cấu hình route sai: vẫn lưu kết quả, nhưng phải thấy trong log
                log.error("judge model has no price", extra={"model": result.served_by.model})
                cost = None
            verdict, sendable = finalize(None, result.parsed)
            await s.execute(
                text(
                    "UPDATE shadow_comparisons SET judge = CAST(:j AS jsonb), judge_model = :m, "
                    "judge_prompt_version = :pv, judge_cost_micro = :cost, judged_at = now(), "
                    "final_verdict = :v, sendable = :sd WHERE id = :id"
                ),
                {
                    "j": result.parsed.model_dump_json(),
                    "m": result.served_by.model,
                    "pv": JUDGE_PROMPT_VERSION,
                    "cost": cost,
                    "v": verdict,
                    "sd": sendable,
                    "id": c.id,
                },
            )
        SHADOW_COMPARISONS.labels(status="judged", verdict=verdict or "").inc()
        await asyncio.sleep(self._pause)
        return True

    @staticmethod
    async def _mark(s: AsyncSession, cid: Any, **fields: Any) -> None:
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        await s.execute(
            text(f"UPDATE shadow_comparisons SET {sets} WHERE id = :id"),  # noqa: S608 - tên cột cố định
            fields | {"id": cid},
        )

    async def run(self, stop: asyncio.Event, poll_s: float = 30.0) -> None:
        while not stop.is_set():
            try:
                busy = await self.process_once()
            except Exception:  # lỗi bất ngờ không được làm chết agent worker
                log.exception("judge worker iteration failed")
                busy = False
            if not busy:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_s)
                except TimeoutError:
                    pass
