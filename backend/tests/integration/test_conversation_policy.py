"""Bước 2.7: tầng chính sách hội thoại trước agent runtime, và API cấu hình/mô phỏng."""

from datetime import datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.modules.agent_runtime.infrastructure.run_queries import get_run
from app.modules.conversation.application.staff_conversation import SendStaffMessage
from app.modules.conversation.infrastructure.unit_of_work import SqlConversationUnitOfWork
from app.modules.conversation_policy.domain.hours import BusinessHours
from app.modules.conversation_policy.domain.policy import (
    PolicyConfig,
    SituationConfig,
    WelcomeConfig,
)
from app.modules.conversation_policy.infrastructure.repository import publish
from app.modules.conversation_policy.stage import PolicyStage

from .test_agent_runtime import make_runner, make_world, one_run, request_for
from .test_staff_api import CSRF, logged_in

VN = ZoneInfo("Asia/Ho_Chi_Minh")
SAT_NIGHT = datetime(2026, 11, 21, 22, 10, tzinfo=VN)
SAT_MORNING = datetime(2026, 11, 21, 10, 0, tzinfo=VN)
WEEK = {d: [(time(8), time(21))] for d in ("mon", "tue", "wed", "thu", "fri", "sat")}


def config(after_next="bot") -> PolicyConfig:
    return PolicyConfig(
        hours=BusinessHours(weekly=WEEK),
        after_hours=SituationConfig(
            enabled=True, text="Dạ shop mở lại lúc {next_open} ạ.", next=after_next
        ),
        welcome=WelcomeConfig(enabled=True, text="Dạ chào bạn ạ!"),
    )


async def with_release(session_factory, w, cfg: PolicyConfig) -> None:
    async with session_factory() as s, s.begin():
        await publish(s, w.tenant_id, cfg, uuid4())


def stage(session_factory, runner, now):
    return PolicyStage(session_factory, runner.handle, clock=lambda: now)


async def decisions(session_factory) -> list:
    async with session_factory() as s:
        rows = await s.execute(text("SELECT * FROM policy_decisions ORDER BY created_at"))
        return rows.mappings().all()


async def handling(session_factory, conversation_id) -> str:
    async with session_factory() as s:
        return (
            await s.execute(
                text("SELECT handling FROM conversations WHERE id = :c"), {"c": conversation_id}
            )
        ).scalar_one()


async def test_no_release_means_no_decision_and_agent_runs(session_factory):
    w = await make_world(session_factory)
    runner, _ = make_runner(session_factory, [])
    await stage(session_factory, runner, SAT_NIGHT).handle(
        await request_for(session_factory, w, ("alo shop",))
    )
    assert await decisions(session_factory) == []
    assert (await one_run(session_factory))["status"] == "completed"


async def test_after_hours_is_recorded_not_sent_and_shown_in_trace(session_factory):
    w = await make_world(session_factory)
    await with_release(session_factory, w, config())
    runner, _ = make_runner(session_factory, [])
    req = await request_for(session_factory, w, ("alo shop",))
    await stage(session_factory, runner, SAT_NIGHT).handle(req)

    [d] = await decisions(session_factory)
    assert (d["situation"], d["run_agent"], d["sent"]) == ("after_hours", True, False)
    assert d["canned_text"] == "Dạ shop mở lại lúc 08:00 thứ Hai (23/11) ạ."
    async with session_factory() as s:  # không có tin nào ngoài tin khách
        senders = (await s.execute(text("SELECT sender_type FROM messages"))).scalars().all()
        detail = await get_run(s, w.tenant_id, (await one_run(session_factory))["id"])
    assert senders == ["customer"]
    assert detail["policy"]["situation"] == "after_hours"
    assert detail["policy"]["release_number"] == 1


async def test_waiting_human_stops_the_agent_without_llm(session_factory):
    w = await make_world(session_factory)
    await with_release(session_factory, w, config(after_next="waiting_human"))
    runner, llm = make_runner(session_factory, [])
    req = await request_for(session_factory, w, ("áo khoác gió giá bao nhiêu",))
    await stage(session_factory, runner, SAT_NIGHT).handle(req)

    assert await handling(session_factory, req.conversation_id) == "waiting_human"
    run = await one_run(session_factory)
    assert (run["status"], run["skip_reason"]) == ("skipped", "handling_waiting")
    assert llm.requests == []


async def test_retry_does_not_decide_twice(session_factory):
    w = await make_world(session_factory)
    await with_release(session_factory, w, config())
    runner, _ = make_runner(session_factory, [])
    req = await request_for(session_factory, w, ("alo shop",))
    st = stage(session_factory, runner, SAT_MORNING)
    await st.handle(req)
    await st.handle(req)  # worker chạy lại cùng yêu cầu
    [d] = await decisions(session_factory)
    assert d["situation"] == "welcome"


async def test_staff_reply_hands_conversation_to_human(session_factory):
    w = await make_world(session_factory)
    runner, llm = make_runner(session_factory, [])
    req = await request_for(session_factory, w, ("áo khoác gió giá bao nhiêu",))
    await SendStaffMessage(lambda: SqlConversationUnitOfWork(session_factory))(
        w.ctx,
        req.conversation_id,
        user_id=uuid4(),
        client_message_id=str(uuid4()),
        text="Dạ em xem giúp chị nhé",
        trace_id=None,
    )
    assert await handling(session_factory, req.conversation_id) == "human"
    await stage(session_factory, runner, SAT_MORNING).handle(req)
    assert (await one_run(session_factory))["skip_reason"] == "handling_human"
    assert llm.requests == []


# ---------- API ----------


async def test_policy_api_save_restore_simulate(app_client, session_factory):
    client, ctx, _, _ = await logged_in(
        lambda **o: app_client("https://api.test", **o), session_factory, role="owner"
    )
    r = await client.get("/api/v1/policy")
    assert r.status_code == 200
    body = r.json()
    assert body["release"] is None and body["suggested"]["welcome"]["enabled"] is True

    cfg = config().model_dump(mode="json")
    first = await client.put("/api/v1/policy", json=cfg, headers=CSRF)
    assert first.status_code == 200, first.text
    cfg["welcome"]["text"] = "Chào {shop_name}!"
    second = await client.put("/api/v1/policy", json=cfg, headers=CSRF)
    assert (first.json()["number"], second.json()["number"]) == (1, 2)

    restored = await client.post(
        f"/api/v1/policy/releases/{first.json()['id']}/restore", headers=CSRF
    )
    assert restored.json()["number"] == 1
    releases = (await client.get("/api/v1/policy/releases")).json()["items"]
    assert [(x["number"], x["current"]) for x in releases] == [(2, False), (1, True)]

    cfg["welcome"]["text"] = "Chào {customer_name}"
    assert (await client.put("/api/v1/policy", json=cfg, headers=CSRF)).status_code == 422

    sim = await client.post(
        "/api/v1/policy/simulate",
        json={"at": SAT_NIGHT.isoformat(), "is_new_conversation": True},
        headers=CSRF,
    )
    assert sim.status_code == 200, sim.text
    assert sim.json()["situation"] == "after_hours"
    assert sim.json()["text"] == "Dạ shop mở lại lúc 08:00 thứ Hai (23/11) ạ."


async def test_agent_role_cannot_change_policy(app_client, session_factory):
    client, *_ = await logged_in(
        lambda **o: app_client("https://api.test", **o), session_factory, role="agent"
    )
    r = await client.put("/api/v1/policy", json=config().model_dump(mode="json"), headers=CSRF)
    assert r.status_code == 403


async def test_return_to_bot_uses_version(app_client, session_factory):
    client, ctx, _, conn = await logged_in(
        lambda **o: app_client("https://api.test", **o), session_factory, role="agent"
    )
    from .test_staff_api import inbound, reply

    res = await inbound(session_factory, ctx, conn)
    assert (await reply(client, res.conversation_id)).status_code in (200, 201)
    conv = (await client.get(f"/api/v1/conversations/{res.conversation_id}")).json()
    assert conv["handling"] == "human"

    url = f"/api/v1/conversations/{res.conversation_id}/return-to-bot"
    stale = await client.post(url, json={"version": conv["version"] - 1}, headers=CSRF)
    assert stale.status_code == 409
    ok = await client.post(url, json={"version": conv["version"]}, headers=CSRF)
    assert ok.status_code == 200
    assert await handling(session_factory, res.conversation_id) == "bot"
