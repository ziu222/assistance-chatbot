"""Giám khảo chạy nền (thiết kế 04 S2): chấm cặp code chưa kết luận, trần chi phí, lấy mẫu."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.modules.agent_runtime.llm.fake import FakeLLM
from app.modules.agent_runtime.llm.port import ProviderUnavailable
from app.modules.shadow.domain.judge import JudgeOut
from app.modules.shadow.infrastructure.comparisons import compare_due
from app.modules.shadow.infrastructure.judging import JudgeWorker

from .test_agent_runtime import result
from .test_shadow_comparisons import comparison, drafted, staff_says


def verdict(v="equivalent", s="as_is"):
    return result(
        JudgeOut(
            verdict=v,
            sendable=s,
            missing_info=False,
            contradicts=False,
            tone_ok=True,
            rationale="Cùng giá và cùng sản phẩm.",
        )
    )


def worker(session_factory, script, **over):
    opts = dict(daily_cap_micro=10_000_000, full_per_day=100, per_intent_after=10, pause_s=0)
    llm = FakeLLM(script)
    return JudgeWorker(session_factory, llm, **(opts | over)), llm


async def paired(session_factory, reply="Dạ áo khoác gió 350k nha chị"):
    w, conv = await drafted(session_factory)
    now = datetime.now(UTC)
    await staff_says(session_factory, w, conv, reply, now)
    await compare_due(session_factory, now=now + timedelta(minutes=5))
    return w


async def drain(judge):
    while await judge.process_once():
        pass


async def test_judge_scores_open_pairs_and_records_cost(session_factory):
    w = await paired(session_factory)
    judge, llm = worker(session_factory, [verdict()] * 20)
    await drain(judge)
    [c] = await comparison(session_factory, w)
    assert (c["final_verdict"], c["sendable"]) == ("equivalent", "as_is")
    assert c["judge"]["rationale"] and c["judge_model"] and c["judged_at"]
    req = next(r for r in llm.requests if "350k nha chị" in r.turns[0].text)
    assert req.route == "judge" and "<draft>" in req.turns[0].text
    async with session_factory() as s:
        spent = (
            await s.execute(
                text(
                    "SELECT sum(cost_micro) FROM usage_events "
                    "WHERE tenant_id = :t AND purpose = 'judge'"
                ),
                {"t": w.tenant_id},
            )
        ).scalar_one()
    assert spent > 0


async def test_code_conclusions_are_not_sent_to_the_judge(session_factory):
    w, conv = await drafted(session_factory)
    async with session_factory() as s, s.begin():
        await s.execute(
            text("UPDATE agent_drafts SET grounded = false WHERE conversation_id = :c"), {"c": conv}
        )
    now = datetime.now(UTC)
    await staff_says(session_factory, w, conv, "Dạ 350k ạ", now)
    await compare_due(session_factory, now=now + timedelta(minutes=5))
    [c] = await comparison(session_factory, w)
    assert (c["final_verdict"], c["sendable"], c["judged_at"]) == (
        "not_sendable",
        "must_not_send",
        None,
    )


async def test_daily_cap_and_provider_errors(session_factory):
    w = await paired(session_factory)
    capped, _ = worker(session_factory, [], daily_cap_micro=0)
    await drain(capped)
    [c] = await comparison(session_factory, w)
    assert c["judge_skipped"] == "daily_cap"

    w2 = await paired(session_factory)
    flaky, _ = worker(session_factory, [ProviderUnavailable("anthropic", "overloaded")] * 30)
    await drain(flaky)
    [c2] = await comparison(session_factory, w2)
    assert (c2["judge_attempts"], c2["judge_skipped"]) == (3, "gave_up")
