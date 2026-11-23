"""Bước 2.12: harness eval chạy được trên DB thật với LLM giả (không gọi mạng)."""

import json
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parents[2] / "evals"))

from run_eval import SIM, GenericFake, load_shop, run_task, sample  # noqa: E402
from scoring import score  # noqa: E402

from app.modules.agent_runtime.runner import AgentRunner  # noqa: E402

TASKS = json.loads((SIM / "tasks.json").read_text(encoding="utf-8"))


def task(task_id: str) -> dict:
    return next(t for t in TASKS if t["id"] == task_id)


def first(pred) -> dict:
    return next(t for t in sorted(TASKS, key=lambda t: t["id"]) if pred(t))


async def test_shop_is_loaded_with_its_documents(session_factory):
    shop = await load_shop(session_factory, "beauty_mh")  # chỉ có FAQ, không file chính sách
    assert shop.profile == "chỉ FAQ" and shop.source == "manual_sparse"
    async with session_factory() as s:
        counts = (
            await s.execute(
                text(
                    "SELECT (SELECT count(*) FROM faq_entries WHERE tenant_id = :t), "
                    "(SELECT count(*) FROM knowledge_docs WHERE tenant_id = :t), "
                    "(SELECT count(DISTINCT sku) FROM catalog_items WHERE tenant_id = :t)"
                ),
                {"t": shop.tenant_id},
            )
        ).one()
    assert counts[0] > 0 and counts[1] == 0 and counts[2] == len(set(shop.sku_by_id.values()))


async def test_tasks_run_through_policy_and_agent(session_factory):
    human = first(lambda t: t["category"] == "human_request")
    paused = first(lambda t: t["category"] == "paused")
    multi = first(lambda t: t["category"] == "multi_turn")
    llm = GenericFake()
    handler = AgentRunner(session_factory, llm).handle
    results, shops = {}, {}
    for t in (human, paused, multi):
        if t["shop"] not in shops:
            shops[t["shop"]] = await load_shop(session_factory, t["shop"])
        shop = shops[t["shop"]]
        o = await run_task(session_factory, shop, t, handler)
        results[t["category"]] = (o, score(t["expected"], o))

    o, s = results["human_request"]
    assert s.needs_human  # cụm từ đòi gặp người ⇒ ticket ở tầng chính sách
    o, s = results["paused"]
    assert s.situation and o.situation == "paused"
    o, s = results["multi_turn"]
    assert o.run_status is not None and o.latency_ms is not None
    async with session_factory() as s_:  # lịch sử nhiều lượt được ghi đúng thứ tự
        n = (await s_.execute(text("SELECT max(sequence) FROM messages"))).scalar_one()
    assert n >= len(multi["history"]) + len(multi["messages"])


def test_sample_spreads_categories_and_shops():
    picked = sample(TASKS, 2)
    by_cat = {}
    for t in picked:
        by_cat.setdefault(t["category"], []).append(t["shop"])
    assert all(len(v) == 2 for v in by_cat.values())
    assert len({t["shop"] for t in picked}) >= 8
    assert sample(TASKS, 2) == picked  # xác định
