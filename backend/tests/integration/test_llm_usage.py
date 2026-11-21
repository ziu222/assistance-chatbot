"""Ghi usage/chi phí LLM: đúng tiền, đúng tenant, không bao giờ ghi 0 ngầm."""

import dataclasses

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.agent_runtime.infrastructure.usage import record_usage, spent_today_micro
from app.modules.agent_runtime.llm.fake import text_result
from app.modules.agent_runtime.llm.port import FailedAttempt, Target, Usage
from app.modules.agent_runtime.llm.pricing import PRICE_VERSION, UnknownPrice

from .factories import make_tenant

SONNET = Target("anthropic", "claude-sonnet-5", max_tokens=4096, effort="medium")
GPT = Target("openai", "gpt-4.1", max_tokens=2048)


def result(target: Target, usage: Usage, failed=()):
    return dataclasses.replace(
        text_result("x"), served_by=target, usage=usage, failover_from=tuple(failed)
    )


def metric(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def rows(session_factory):
    async with session_factory() as s:
        return (await s.execute(text("SELECT * FROM usage_events ORDER BY id"))).mappings().all()


async def test_record_usage_stores_tokens_cost_and_price_version(session_factory):
    async with session_factory() as s:
        ctx = await make_tenant(s)
        cost = await record_usage(
            s,
            tenant_id=ctx.tenant_id,
            run_id=None,
            route="draft",
            result=result(SONNET, Usage(input=1000, cache_read=9000, cache_write=500, output=200)),
        )
        await s.commit()

    # 1000×2 + 9000×0,2 + 500×2,5 + 200×10 = 7050 micro-USD
    assert cost == 7050
    [row] = await rows(session_factory)
    assert (row["purpose"], row["provider"], row["model"]) == (
        "draft",
        "anthropic",
        "claude-sonnet-5",
    )
    assert (row["input_tokens"], row["cache_read_tokens"], row["cache_write_tokens"]) == (
        1000,
        9000,
        500,
    )
    assert (row["output_tokens"], row["cost_micro"], row["price_version"]) == (
        200,
        7050,
        PRICE_VERSION,
    )
    assert row["failed_over_from"] is None and row["tenant_id"] == ctx.tenant_id


async def test_failover_is_recorded_on_the_serving_row(session_factory):
    failed = [FailedAttempt(SONNET, "timeout"), FailedAttempt(SONNET, "circuit_open")]
    async with session_factory() as s:
        ctx = await make_tenant(s)
        await record_usage(
            s,
            tenant_id=ctx.tenant_id,
            run_id=None,
            route="classify",
            result=result(GPT, Usage(input=100, output=10), failed),
        )
        await s.commit()
    [row] = await rows(session_factory)
    assert row["provider"] == "openai"
    assert row["failed_over_from"] == "anthropic:timeout,anthropic:circuit_open"


async def test_unknown_price_raises_and_writes_nothing(session_factory):
    ghost = Target("openai", "gpt-9-imaginary", max_tokens=100)
    async with session_factory() as s:
        ctx = await make_tenant(s)
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(UnknownPrice):
            await record_usage(
                s,
                tenant_id=ctx.tenant_id,
                run_id=None,
                route="draft",
                result=result(ghost, Usage(input=100)),
            )
        await s.commit()
    assert await rows(session_factory) == []  # không có dòng chi phí 0 làm lách trần ngày


async def test_metrics_count_tokens_and_cost(session_factory):
    before_cost = metric("llm_cost_micro_total", provider="openai")
    before_out = metric("llm_tokens_total", provider="openai", kind="output")
    async with session_factory() as s:
        ctx = await make_tenant(s)
        cost = await record_usage(
            s,
            tenant_id=ctx.tenant_id,
            run_id=None,
            route="draft",
            result=result(GPT, Usage(input=1_000_000, output=1_000)),
        )
        await s.commit()
    assert metric("llm_cost_micro_total", provider="openai") == before_cost + cost
    assert metric("llm_tokens_total", provider="openai", kind="output") == before_out + 1000


async def test_spent_today_sums_only_this_tenant_and_only_today(session_factory):
    async with session_factory() as s:
        a, b = await make_tenant(s), await make_tenant(s)
        for ctx, n in ((a, 1), (a, 2), (b, 5)):
            await record_usage(
                s,
                tenant_id=ctx.tenant_id,
                run_id=None,
                route="draft",
                result=result(GPT, Usage(input=n * 1_000_000)),  # n × 2.000.000 micro
            )
        await s.commit()
        # một dòng của tenant a thuộc về ngày hôm qua: không được tính vào trần hôm nay
        await s.execute(
            text(
                "UPDATE usage_events SET created_at = "
                "(date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') "
                "- interval '1 second' "
                "WHERE tenant_id = :t AND input_tokens = 1000000"
            ),
            {"t": a.tenant_id},
        )
        await s.commit()

        assert await spent_today_micro(s, a.tenant_id) == 4_000_000  # chỉ dòng 2×2.000.000
        assert await spent_today_micro(s, b.tenant_id) == 10_000_000
        assert await spent_today_micro(s, (await make_tenant(s)).tenant_id) == 0


@pytest.mark.parametrize("column", ["input_tokens", "cost_micro"])
async def test_database_rejects_negative_values(session_factory, column):
    values = {
        "input_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 0,
        "cost_micro": 0,
    } | {column: -1}
    async with session_factory() as s:
        ctx = await make_tenant(s)
        await s.commit()
    async with session_factory() as s:
        with pytest.raises(IntegrityError):
            await s.execute(
                text(
                    "INSERT INTO usage_events (tenant_id, purpose, provider, model, "
                    "input_tokens, cache_read_tokens, cache_write_tokens, output_tokens, "
                    "cost_micro, price_version) VALUES (:t, 'draft', 'openai', 'gpt-4.1', "
                    ":input_tokens, :cache_read_tokens, :cache_write_tokens, :output_tokens, "
                    ":cost_micro, 'v')"
                ),
                {"t": ctx.tenant_id, **values},
            )
