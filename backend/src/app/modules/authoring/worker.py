"""Xử lý yêu cầu gợi ý viết trong agent worker (nơi duy nhất giữ key LLM, thiết kế §9).

Một yêu cầu = một lần gọi route `authoring`. Lỗi không thử lại tự động: chủ shop bấm lại nếu
muốn (yêu cầu tương tác, không phải hàng đợi bền như run của agent).
"""

import asyncio
import logging
import time
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.agent_runtime.infrastructure.usage import record_usage, spent_today_micro
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
from app.modules.authoring.domain.facts import check
from app.modules.authoring.domain.writing import (
    BrandProfile,
    WritingOut,
    system_blocks,
    user_turn,
)
from app.modules.authoring.infrastructure import repository as repo

log = logging.getLogger(__name__)
ROUTE = "authoring"
DEADLINE_S = 30


class AuthoringWorker:
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
        """True nếu đã xử lý một yêu cầu (để vòng lặp chạy tiếp ngay)."""
        async with self._factory() as s, s.begin():
            req = await repo.claim_next(s)
            if req is None:
                return False
            spent = await spent_today_micro(s, req.tenant_id, authoring=True)
            found = await repo.current_profile(s, req.tenant_id)
        profile = found[1] if found else BrandProfile()
        if spent >= self._cap:
            await self._finish(req.id, error_code="daily_cap")
            return True
        try:
            result = await self._llm.complete(
                LLMRequest(
                    route=ROUTE,
                    system_blocks=system_blocks(profile, req.level, req.kind),  # type: ignore[arg-type]
                    turns=(Turn(role="user", text=user_turn(req.text)),),
                    output_schema=WritingOut,
                    deadline=self._clock() + DEADLINE_S,
                )
            )
        except (AllTargetsFailed, ProviderUnavailable, DeadlineExceeded):
            await self._finish(req.id, error_code="llm_unavailable")
            return True
        except (BadRequest, InvalidOutput):
            await self._finish(req.id, error_code="invalid_output")
            return True
        if result.stop_reason == "refusal" or not isinstance(result.parsed, WritingOut):
            await self._finish(req.id, error_code="refusal")
            return True
        suggestions = [
            {
                "text": sg.text,
                "changes": sg.changes,
                "fact_check": check(req.text, sg.text).as_json(),
            }
            for sg in result.parsed.suggestions
        ]
        async with self._factory() as s, s.begin():
            try:
                await record_usage(
                    s, tenant_id=req.tenant_id, run_id=None, route=ROUTE, result=result
                )
            except UnknownPrice:  # cấu hình route sai: vẫn trả gợi ý, nhưng phải thấy trong log
                log.error("authoring model has no price", extra={"model": result.served_by.model})
            await repo.finish(s, req.id, suggestions=suggestions)
        return True

    async def _finish(self, rid, *, error_code: str) -> None:
        async with self._factory() as s, s.begin():
            await repo.finish(s, rid, error_code=error_code)

    async def run(self, stop: asyncio.Event, poll_s: float = 1.0) -> None:
        while not stop.is_set():
            try:
                busy = await self.process_once()
            except Exception:  # lỗi bất ngờ không được làm chết agent worker
                log.exception("authoring worker iteration failed")
                busy = False
            if not busy:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_s)
                except TimeoutError:
                    pass
