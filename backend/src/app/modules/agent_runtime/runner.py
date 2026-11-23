"""Agent runtime: một run từ yêu cầu của worker tới bản nháp có nguồn (thiết kế bước 2 §7.3).

Bất biến an toàn (§1.4): file này, và cả module `agent_runtime`, không có đường nào ghi vào
`messages` hay gửi tin tới khách. Output duy nhất là hàng `agent_drafts`; test kiến trúc chặn việc
import use case gửi tin. Lỗi tạm thời (provider sập) ném lại để worker retry; lỗi không thử lại
được thì đóng run `failed` rồi trả về bình thường để hàng đợi không kẹt.
"""

import asyncio
import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.agent_management.infrastructure.repository import get_ai_settings
from app.modules.agent_runtime.context import RunContext, build_context
from app.modules.agent_runtime.infrastructure.run_requests import RunRequest
from app.modules.agent_runtime.infrastructure.runs import (
    AgentForRun,
    DraftToSave,
    Step,
    Totals,
    emit_completed,
    finish_run,
    load_agent,
    lock_for_save,
    record_knowledge_gap,
    save_draft,
    start_run,
)
from app.modules.agent_runtime.infrastructure.usage import record_usage, spent_today_micro
from app.modules.agent_runtime.llm.port import (
    AllTargetsFailed,
    BadRequest,
    DeadlineExceeded,
    InvalidOutput,
    LLMPort,
    LLMRequest,
    LLMResult,
    ProviderUnavailable,
    Turn,
)
from app.modules.agent_runtime.llm.port import ToolResult as LLMToolResult
from app.modules.agent_runtime.llm.pricing import UnknownPrice
from app.modules.agent_runtime.order_step import (
    OrderContext,
    apply_draft,
    load_order,
    with_phone,
)
from app.modules.agent_runtime.prompts import CLASSIFY, DRAFT, PROMPT_VERSION, SAFETY
from app.modules.agent_runtime.rules import apply_rules
from app.modules.agent_runtime.schemas import ClassifyOut, DraftOut, OrderDraftOut
from app.modules.agent_runtime.signals import behavior_signals, score, semantic_signals
from app.modules.agent_runtime.tools import TOOLS, run_tool
from app.modules.agent_runtime.validation import extract_amounts, validate_draft
from app.modules.knowledge.domain.topics import suggest_topics
from app.modules.orders.infrastructure.repository import OrderRequest, save_request
from app.shared.infrastructure.metrics import (
    AGENT_DRAFTS,
    AGENT_NO_LLM,
    AGENT_RUN_DURATION,
    AGENT_RUNS,
)

log = logging.getLogger(__name__)

_WITH_OUTPUT = ("completed", "handoff_suggested")


class _Stop(Exception):
    """Kết thúc run sớm với một trạng thái cuối đã biết (không phải lỗi)."""

    def __init__(
        self,
        status: str,
        *,
        skip_reason: str | None = None,
        error_code: str | None = None,
        draft: DraftToSave | None = None,
    ) -> None:
        self.status, self.skip_reason, self.error_code, self.draft = (
            status,
            skip_reason,
            error_code,
            draft,
        )


@dataclass
class _Run:
    tenant_id: UUID
    conversation_id: UUID
    from_sequence: int
    to_sequence: int
    run_id: UUID
    agent: AgentForRun
    deadline: float
    totals: Totals = field(default_factory=Totals)
    steps: list[Step] = field(default_factory=list)
    intents: list[str] | None = None
    ctx: RunContext | None = None
    order: OrderContext | None = None  # luồng đặt hàng (thiết kế 03): None = không áp dụng
    order_update: OrderRequest | None = None  # phiếu sau lượt này, ghi cùng nháp
    products: dict[str, dict] = field(default_factory=dict)  # sku ⇒ bản ghi tool đã trả
    guard: bool = True  # kiểm tra lại mode/kill switch/last_sequence ngay trước khi lưu

    @property
    def knowledge_hash(self) -> str:
        return self.ctx.knowledge_hash if self.ctx else ""


class AgentRunner:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMPort,
        *,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._factory = session_factory
        self._llm = llm
        self._rng = rng

    async def handle(self, req: RunRequest) -> None:
        """Handler của AgentWorker. Ném lỗi ⇒ worker hẹn chạy lại; trả về ⇒ yêu cầu được xóa."""
        started = time.monotonic()
        async with self._factory() as s, s.begin():
            agent = await load_agent(s, req.tenant_id, req.conversation_id)
            if agent is None:  # kênh chưa có agent: không có gì để chạy
                log.info(
                    "no agent for conversation", extra={"conversation_id": str(req.conversation_id)}
                )
                return
            run_id = await start_run(
                s,
                tenant_id=req.tenant_id,
                agent=agent,
                conversation_id=req.conversation_id,
                from_sequence=req.from_sequence,
                to_sequence=req.to_sequence,
                prompt_version=PROMPT_VERSION,
            )
        run = _Run(
            req.tenant_id,
            req.conversation_id,
            req.from_sequence,
            req.to_sequence,
            run_id,
            agent,
            deadline=time.monotonic() + agent.template.budget.wall_clock_s,
        )
        retry: Exception | None = None
        try:
            async with asyncio.timeout(agent.template.budget.wall_clock_s):
                await self._execute(run)
        except _Stop as stop:
            await self._finish(run, stop.status, stop.skip_reason, stop.error_code, stop.draft)
        except (TimeoutError, DeadlineExceeded):
            run.guard = False
            await self._finish(run, "timed_out", None, "wall_clock", None)
        except (BadRequest, InvalidOutput, UnknownPrice) as exc:
            run.guard = False  # không thử lại được: đóng run, hàng đợi đi tiếp
            await self._finish(run, "failed", None, type(exc).__name__.lower(), None)
        except (AllTargetsFailed, ProviderUnavailable) as exc:
            run.guard = False
            await self._finish(run, "failed", None, "llm_unavailable", None)
            retry = exc
        except Exception as exc:
            run.guard = False
            await self._finish(run, "failed", None, "internal", None)
            retry = exc
        AGENT_RUN_DURATION.observe(time.monotonic() - started)
        if retry is not None:
            raise retry

    # --- các bước của một run (§7.3) ---

    async def _execute(self, run: _Run) -> None:
        settings, spent = await self._load_gate_inputs(run)
        tpl, agent = run.agent.template, run.agent
        # 0. Cổng: không gọi LLM, không đọc dữ liệu khách.
        gate = None
        if agent.handling != "bot":  # người đang/sắp xử lý: bot im (§12.3)
            gate = "handling_human" if agent.handling == "human" else "handling_waiting"
        elif not settings.ai_enabled:
            gate = "kill_switch"
        elif agent.mode != "shadow":
            gate = "mode_manual"
        elif spent >= settings.daily_cost_cap_micro:
            gate = "daily_cap"
        elif agent.sample_rate < 1 and self._rng() >= float(agent.sample_rate):
            gate = "sampled_out"
        run.steps.append(Step("rule", "gate", gate or "pass", 0, {"skip_reason": gate}))
        if gate:
            run.guard = False
            raise _Stop("skipped", skip_reason=gate)

        # 1. Ngữ cảnh (xác định, chỉ text).
        async with self._factory() as s:
            ctx = run.ctx = await build_context(
                s, run.tenant_id, run.conversation_id, run.from_sequence, run.to_sequence
            )
        run.steps.append(
            Step(
                "rule",
                "context",
                "ok",
                0,
                {
                    "history": len(ctx.history),
                    "new": len(ctx.new),
                    "knowledge_items": len(ctx.knowledge_sources),
                },
            )
        )
        if not ctx.customer_texts:
            run.guard = False
            raise _Stop("skipped", skip_reason="no_customer_message")

        # Tín hiệu leo thang theo hành vi: chỉ ghi để đo (§12.7), không đổi luồng.
        behavior = behavior_signals(
            [m.content for m in ctx.history if m.who == "khach"],
            ctx.customer_texts,
            sum(1 for m in ctx.history if m.who != "khach"),
        )
        run.steps.append(
            Step(
                "signal", "behavior", "recorded", 0, {"signals": behavior, "score": score(behavior)}
            )
        )

        # 2. Rule: không tốn LLM.
        outcome = apply_rules(ctx.customer_texts)
        if outcome is not None:
            run.steps.append(Step("rule", outcome.kind, "matched", 0, {"match": outcome.matched}))
            if outcome.kind == "handoff":
                raise _Stop("handoff_suggested", skip_reason=None)
            raise _Stop("completed", draft=DraftToSave(outcome.reply, [], True, False, None))
        run.steps.append(Step("rule", "router", "no_match", 0))

        # 3. Phân loại.
        classify = await self._call(
            run,
            "classify",
            LLMRequest(
                route=tpl.routes.classify,
                system_blocks=(
                    SAFETY,
                    CLASSIFY.format(
                        intents="\n".join(f"- {k}: {v}" for k, v in tpl.intents.items())
                    ),
                ),
                turns=(Turn(role="user", text=ctx.transcript()),),
                output_schema=ClassifyOut,
                deadline=run.deadline,
            ),
        )
        if classify.stop_reason == "refusal":
            raise _Stop("handoff_suggested")
        parsed = classify.parsed
        if not isinstance(parsed, ClassifyOut):
            raise InvalidOutput("classify: no parsed output")
        run.intents = [i for i in parsed.intents if i in tpl.intents] or ["other"]
        semantic = semantic_signals(
            parsed.frustration, parsed.human_decision, len(ctx.history) + len(ctx.new)
        )
        run.steps.append(
            Step(
                "signal",
                "semantic",
                "recorded",
                0,
                {
                    "signals": semantic,
                    "score": score(semantic),
                    "frustration": parsed.frustration,
                    "human_decision": parsed.human_decision,
                },
            )
        )
        if set(run.intents) & set(tpl.handoff_intents):
            raise _Stop("handoff_suggested")

        # 3b. Đặt hàng: shop đã bật quy trình và khách muốn đặt (hoặc đang có phiếu dở).
        async with self._factory() as s:
            run.order = await load_order(
                s, run.tenant_id, run.conversation_id, order_intent="order_intent" in run.intents
            )
        if run.order:
            with_phone(run.order, ctx.customer_texts)
            run.steps.append(
                Step("rule", "order_flow", "active", 0, {"missing": run.order.request.missing})
            )

        # 4. Soạn nháp (vòng tool read-only).
        out, prices, product_ids = await self._draft(run, ctx)

        # 5. Kiểm tra bằng quy tắc xác định.
        started = time.monotonic()
        problems = validate_draft(
            out,
            valid_sources=set(ctx.knowledge_sources) | {("product", p) for p in product_ids},
            known_amounts=prices | extract_amounts(ctx.knowledge_text),
        )
        run.steps.append(
            Step(
                "validate",
                "draft",
                "ok" if not problems else "ungrounded",
                int((time.monotonic() - started) * 1000),
                {"problems": problems},
            )
        )
        needs_human, reason = out.needs_human, out.reason
        if run.order and isinstance(out, OrderDraftOut):
            # Đơn hàng luôn cần nhân viên xác nhận; phiếu được ghi cùng nháp ở bước lưu.
            run.order_update = apply_draft(run.order, out.order, run.products)
            needs_human, reason = True, reason or "order"
            run.steps.append(
                Step(
                    "rule",
                    "order_request",
                    "ready" if not run.order_update.missing else "collecting",
                    0,
                    {"missing": run.order_update.missing, "items": len(run.order_update.items)},
                )
            )
        reply = out.reply.strip()
        if not reply:
            if needs_human:
                raise _Stop("handoff_suggested")
            raise _Stop("failed", error_code="empty_reply")
        draft = DraftToSave(
            reply,
            [s.model_dump() for s in out.sources],
            grounded=not problems,
            needs_human=needs_human,
            reason=reason or ",".join(problems) or None,
        )
        raise _Stop("handoff_suggested" if needs_human else "completed", draft=draft)

    async def _draft(self, run: _Run, ctx: RunContext) -> tuple[DraftOut, set[int], set[str]]:
        tpl = run.agent.template
        budget = tpl.budget
        instructions = tpl.instructions.replace("{shop_name}", run.agent.shop_name)
        system = (SAFETY + "\n" + DRAFT, instructions, ctx.knowledge_block)
        schema: type[DraftOut] = DraftOut
        if run.order:  # khối đổi theo hội thoại: sau khối tri thức đã cache (ADR-0028)
            system += (run.order.block(),)
            schema = OrderDraftOut
        tools = tuple(t for t in TOOLS if t.name in tpl.tools)
        turns = [Turn(role="user", text=ctx.transcript())]
        prices: set[int] = set()
        product_ids: set[str] = set()
        while True:
            if run.totals.model_calls >= budget.max_model_calls or (
                run.totals.cost_micro >= budget.max_cost_micro
            ):
                raise _Stop("failed", error_code="budget_exceeded")
            res = await self._call(
                run,
                "llm",
                LLMRequest(
                    route=tpl.routes.draft,
                    system_blocks=system,
                    turns=tuple(turns),
                    tools=tools,
                    output_schema=schema,
                    deadline=run.deadline,
                ),
            )
            if res.stop_reason == "refusal":
                raise _Stop("handoff_suggested")
            if res.stop_reason == "max_tokens":
                raise _Stop("failed", error_code="max_tokens")
            if res.stop_reason == "end_turn":
                if not isinstance(res.parsed, DraftOut):
                    raise InvalidOutput("draft: no parsed output")
                return res.parsed, prices, product_ids
            # tool_use: backend validate, ép tenant từ run, trả kết quả như dữ liệu.
            if run.totals.tool_calls + len(res.tool_calls) > budget.max_tool_calls:
                raise _Stop("failed", error_code="budget_exceeded")
            results: list[LLMToolResult] = []
            for call in res.tool_calls:
                run.totals.tool_calls += 1
                started = time.monotonic()
                if call.name in tpl.tools:
                    async with self._factory() as s:
                        tr = await run_tool(s, run.tenant_id, call.name, call.arguments)
                else:
                    tr = None
                ok = tr is not None and tr.ok
                if ok:
                    for item in tr.data if isinstance(tr.data, list) else [tr.data]:
                        product_ids.add(item["id"])
                        prices.add(int(item["price_vnd"]))
                        run.products[item["sku"]] = item
                error = "unknown_tool" if tr is None else tr.error
                results.append(
                    LLMToolResult(
                        call.id,
                        json.dumps(tr.data, ensure_ascii=False) if ok else str(error),
                        is_error=not ok,
                    )
                )
                run.steps.append(
                    Step(
                        "tool",
                        call.name,
                        "ok" if ok else str(error),
                        int((time.monotonic() - started) * 1000),
                        # Đã chiếu: tên tham số + số bản ghi, không lưu nội dung khách gõ.
                        {
                            "arg_keys": sorted(call.arguments),
                            "records": len(tr.data)
                            if ok and isinstance(tr.data, list)
                            else int(ok),
                        }
                        | ({"stale_catalog": True} if tr is not None and tr.stale else {}),
                    )
                )
            turns.append(Turn(role="assistant", text=res.text, tool_calls=res.tool_calls))
            turns.append(Turn(role="tool_results", tool_results=tuple(results)))

    async def _call(self, run: _Run, kind: str, req: LLMRequest) -> LLMResult:
        started = time.monotonic()
        result = await self._llm.complete(req)
        async with self._factory() as s, s.begin():
            cost = await record_usage(
                s, tenant_id=run.tenant_id, run_id=run.run_id, route=req.route, result=result
            )
        t, u = run.totals, result.usage
        t.model_calls += 1
        t.input_tokens += u.input
        t.cache_read_tokens += u.cache_read
        t.cache_write_tokens += u.cache_write
        t.output_tokens += u.output
        t.cost_micro += cost
        run.steps.append(
            Step(
                kind,
                req.route,
                result.stop_reason,
                int((time.monotonic() - started) * 1000),
                {
                    "provider": result.served_by.provider,
                    "model": result.served_by.model,
                    "failover_from": [
                        a.target.label + ":" + a.reason for a in result.failover_from
                    ],
                    "tokens": {"in": u.input, "cache_read": u.cache_read, "out": u.output},
                },
            )
        )
        return result

    async def _load_gate_inputs(self, run: _Run):
        async with self._factory() as s:
            return (
                await get_ai_settings(s, run.tenant_id),
                await spent_today_micro(s, run.tenant_id),
            )

    # --- bước 6: lưu ---

    async def _finish(
        self,
        run: _Run,
        status: str,
        skip_reason: str | None,
        error_code: str | None,
        draft: DraftToSave | None,
    ) -> None:
        async with self._factory() as s, s.begin():
            if run.guard and status in _WITH_OUTPUT:
                # Kiểm tra lại ngay trước khi lưu, dưới khóa: bất biến 3 và §7.5.
                guard = await lock_for_save(s, run.tenant_id, run.conversation_id)
                ai_on = (await get_ai_settings(s, run.tenant_id)).ai_enabled
                if guard.mode != "shadow":
                    status, skip_reason, draft = "skipped", "mode_changed", None
                elif not ai_on:
                    status, skip_reason, draft = "skipped", "kill_switch", None
                elif run.ctx and guard.last_sequence != run.ctx.last_sequence:
                    status, skip_reason, draft = "superseded", None, None
            draft_id = None
            if status in _WITH_OUTPUT:
                if draft is not None:
                    draft_id = await save_draft(
                        s,
                        tenant_id=run.tenant_id,
                        run_id=run.run_id,
                        conversation_id=run.conversation_id,
                        based_on_sequence=run.to_sequence,
                        draft=draft,
                    )
                    if run.order and run.order_update:
                        await save_request(
                            s,
                            run.tenant_id,
                            run.conversation_id,
                            flow_release_id=run.order.release_id,
                            channel=run.order.provider,
                            req=run.order_update,
                        )
                    AGENT_DRAFTS.labels(
                        grounded=str(draft.grounded).lower(),
                        needs_human=str(draft.needs_human).lower(),
                    ).inc()
                    if draft.needs_human and "no_source" in (draft.reason or "") and run.ctx:
                        await record_knowledge_gap(
                            s,
                            tenant_id=run.tenant_id,
                            conversation_id=run.conversation_id,
                            run_id=run.run_id,
                            intents=run.intents,
                            topics=suggest_topics(" ".join(run.ctx.customer_texts)),
                        )
                await emit_completed(
                    s,
                    tenant_id=run.tenant_id,
                    run_id=run.run_id,
                    conversation_id=run.conversation_id,
                    draft_id=draft_id,
                    sequence=run.to_sequence,
                    status=status,
                )
                if run.totals.model_calls == 0:
                    AGENT_NO_LLM.inc()
            await finish_run(
                s,
                tenant_id=run.tenant_id,
                run_id=run.run_id,
                status=status,
                skip_reason=skip_reason,
                error_code=error_code,
                intents=run.intents,
                knowledge_hash=run.knowledge_hash,
                totals=run.totals,
                steps=run.steps,
            )
        AGENT_RUNS.labels(status=status, skip_reason=skip_reason or "").inc()
