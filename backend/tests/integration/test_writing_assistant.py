"""Bước 2.11: trợ lý viết — yêu cầu qua API, agent worker gọi LLM, code kiểm giữ sự thật."""

import dataclasses

from sqlalchemy import text

from app.modules.agent_runtime.infrastructure.usage import spent_today_micro
from app.modules.agent_runtime.llm.fake import FakeLLM, refusal_result, text_result
from app.modules.agent_runtime.llm.port import AllTargetsFailed, LLMRequest
from app.modules.authoring.domain.writing import Suggestion, WritingOut
from app.modules.authoring.worker import AuthoringWorker

from .test_agent_runtime import SONNET, USAGE
from .test_staff_api import CSRF, logged_in

ORIGINAL = "Dạ {shop_name} chào bạn, shop đổi trả trong 7 ngày, ship nội thành 30.000đ nhé"


def writing(*texts: str):
    out = WritingOut(suggestions=[Suggestion(text=t, changes="Trau chuốt câu.") for t in texts])
    return dataclasses.replace(text_result("{}", parsed=out), served_by=SONNET, usage=USAGE)


async def staff(app_client, session_factory, role="owner"):
    client, ctx, *_ = await logged_in(
        lambda **o: app_client("https://api.test", **o), session_factory, role=role
    )
    return client, ctx


async def ask(client, text_=ORIGINAL, level="polish"):
    r = await client.post(
        "/api/v1/writing/suggest",
        json={"kind": "auto_message", "level": level, "text": text_},
        headers=CSRF,
    )
    assert r.status_code == 202, r.text
    return r.json()["id"]


async def test_suggestions_are_fact_checked_and_feedback_recorded(app_client, session_factory):
    client, ctx = await staff(app_client, session_factory)
    rid = await ask(client)
    assert (await client.get(f"/api/v1/writing/requests/{rid}")).json()["status"] == "pending"

    good = "{shop_name} xin chào bạn ạ! Shop hỗ trợ đổi trả trong 7 ngày, ship nội thành 30k."
    bad = "{shop_name} chào bạn! Đổi trả trong 30 ngày, freeship nội thành."
    llm = FakeLLM([writing(good, bad)])
    worker = AuthoringWorker(session_factory, llm, daily_cap_micro=1_000_000)
    assert await worker.process_once() is True
    assert await worker.process_once() is False  # hết việc

    body = (await client.get(f"/api/v1/writing/requests/{rid}")).json()
    assert body["status"] == "done"
    first, second = body["suggestions"]
    assert first["fact_check"]["ok"] is True
    assert second["fact_check"]["ok"] is False
    assert second["fact_check"]["added"]["durations"] == ["30 ngay"]
    assert "freeship" in second["fact_check"]["new_promises"]
    req: LLMRequest = llm.requests[0]
    assert req.route == "authoring" and "<draft>" in req.turns[0].text

    fb = f"/api/v1/writing/requests/{rid}/feedback"
    send = lambda i, a: client.post(fb, json={"index": i, "action": a}, headers=CSRF)  # noqa: E731
    assert (await send(0, "used")).status_code == 200
    assert (await send(1, "used")).status_code == 409  # gợi ý đổi sự thật thì không được "Dùng"
    assert (await send(1, "dismissed")).status_code == 200
    async with session_factory() as s:
        kinds = (await s.execute(text("SELECT kind FROM learning_signals ORDER BY id"))).scalars()
        assert list(kinds) == ["suggestion_used", "suggestion_dismissed"]
        purposes = (await s.execute(text("SELECT purpose FROM usage_events"))).scalars().all()
        assert purposes == ["authoring"]
        # Chi phí trợ lý viết không ăn vào trần chi phí ngày của nháp.
        assert await spent_today_micro(s, ctx.tenant_id) == 0
        assert await spent_today_micro(s, ctx.tenant_id, authoring=True) > 0


async def test_brand_profile_is_versioned_and_used_in_prompt(app_client, session_factory):
    client, _ = await staff(app_client, session_factory)
    default = (await client.get("/api/v1/brand-profile")).json()
    assert default["number"] is None and default["profile"]["shop_pronoun"] == "em"
    profile = {"shop_pronoun": "shop", "customer_pronoun": "bạn", "tone": "tre_trung",
               "emoji": True, "preferred_terms": ["xinh xỉu"], "banned_terms": ["quý khách"],
               "samples": ["Shop chào bạn nha, cần gì cứ nhắn shop liền!"]}  # fmt: skip
    first = await client.put("/api/v1/brand-profile", json=profile, headers=CSRF)
    second = await client.put("/api/v1/brand-profile", json=profile, headers=CSRF)
    assert (first.json()["number"], second.json()["number"]) == (1, 2)
    bad = await client.put("/api/v1/brand-profile", json={"tone": "hài hước"}, headers=CSRF)
    assert bad.status_code == 422

    await ask(client, level="brand")
    llm = FakeLLM([writing("Shop chào bạn nha {shop_name}! Đổi trả 7 ngày, ship 30.000đ")])
    await AuthoringWorker(session_factory, llm, daily_cap_micro=1_000_000).process_once()
    blocks = "\n".join(llm.requests[0].system_blocks)
    assert 'xưng "shop"' in blocks and "quý khách" in blocks and "xinh xỉu" in blocks


async def test_failures_are_reported_not_retried(app_client, session_factory):
    client, ctx = await staff(app_client, session_factory)
    worker_llm = FakeLLM([AllTargetsFailed(()), refusal_result()])
    worker = AuthoringWorker(session_factory, worker_llm, daily_cap_micro=1_000_000)
    down = await ask(client)
    await worker.process_once()
    refused = await ask(client)
    await worker.process_once()
    for rid, code in ((down, "llm_unavailable"), (refused, "refusal")):
        body = (await client.get(f"/api/v1/writing/requests/{rid}")).json()
        assert (body["status"], body["error_code"]) == ("failed", code)

    # Trần chi phí ngày của trợ lý viết: vượt ⇒ không gọi LLM.
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO usage_events (tenant_id, purpose, provider, model, input_tokens, "
                "cache_read_tokens, cache_write_tokens, output_tokens, cost_micro, price_version) "
                "VALUES (:t, 'authoring', 'anthropic', 'x', 0, 0, 0, 0, 500000, 'v')"
            ),
            {"t": ctx.tenant_id},
        )
    capped = await ask(client)
    idle = FakeLLM([])
    await AuthoringWorker(session_factory, idle, daily_cap_micro=300_000).process_once()
    body = (await client.get(f"/api/v1/writing/requests/{capped}")).json()
    assert body["error_code"] == "daily_cap" and idle.requests == []


async def test_limits_and_permissions(app_client, session_factory):
    client, _ = await staff(app_client, session_factory)
    for _ in range(3):
        await ask(client)
    fourth = await client.post("/api/v1/writing/suggest", json={"text": "chào bạn"}, headers=CSRF)
    assert fourth.status_code == 429  # tối đa 3 yêu cầu đang chờ mỗi shop

    agent, _ = await staff(app_client, session_factory, role="agent")
    denied = await agent.post("/api/v1/writing/suggest", json={"text": "chào"}, headers=CSRF)
    assert denied.status_code == 403
