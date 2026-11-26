"""Tổng hợp theo ý định và mức sẵn sàng (thiết kế 04 S4)."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import text

from app.modules.shadow.domain.judge import JUDGE_PROMPT_VERSION

from .factories import make_conversation, make_customer
from .test_staff_api import logged_in

OLD = {"template": "faq_sales_quote", "template_version": "4", "prompt_version": "p1",
       "knowledge_hash": "k1", "model": "claude-sonnet-5"}  # fmt: skip
NEW = OLD | {"template_version": "5", "knowledge_hash": "k2"}


async def add(s, ctx, conv, *, intent, config, status="paired", verdict="equivalent",
              sendable="as_is", when=None, checks=None, label=None):  # fmt: skip
    cid = uuid4()
    judged = status == "paired"
    await s.execute(
        text(
            "INSERT INTO shadow_comparisons (id, tenant_id, conversation_id, draft_id, run_id, "
            "status, intent, config_version, code_checks, final_verdict, sendable, judged_at, "
            "judge_prompt_version, bot_lead_seconds, created_at) VALUES (:id, :t, :c, :d, :r, "
            ":st, :i, CAST(:cv AS jsonb), CAST(:cc AS jsonb), :v, :sd, :ja, :pv, 120, :at)"
        ),
        {
            "id": cid,
            "t": ctx.tenant_id,
            "c": conv,
            "d": uuid4(),
            "r": uuid4(),
            "st": status,
            "i": intent,
            "cv": json.dumps(config),
            "cc": json.dumps(checks or {"amounts": "agree", "handoff": "agree"}),
            "v": verdict if judged else None,
            "sd": sendable if judged else None,
            "ja": datetime.now(UTC) if judged else None,
            "pv": JUDGE_PROMPT_VERSION,
            "at": when or datetime.now(UTC),
        },
    )
    if label:
        await s.execute(
            text(
                "INSERT INTO judge_labels (comparison_id, tenant_id, label, labeled_by) "
                "VALUES (:c, :t, :l, :u)"
            ),
            {"c": cid, "t": ctx.tenant_id, "l": label, "u": uuid4()},
        )


async def test_summary_counts_only_the_current_config_and_explains_readiness(
    app_client, session_factory
):
    client, ctx, _, conn = await logged_in(
        lambda: app_client("https://api.test"), session_factory, role="owner"
    )
    async with session_factory() as s, s.begin():
        conv = await make_conversation(s, ctx, await make_customer(s, ctx), conn)
        week_ago = datetime.now(UTC) - timedelta(days=3)
        for _ in range(10):  # phiên bản cũ toàn sai: không được kéo phiên bản mới xuống
            await add(s, ctx, conv, intent="price_quote", config=OLD, verdict="wrong",
                      sendable="must_not_send", when=week_ago)  # fmt: skip
        for i in range(49):
            await add(s, ctx, conv, intent="price_quote", config=NEW,
                      label="sendable" if i < 30 else None)  # fmt: skip
        await add(s, ctx, conv, intent="price_quote", config=NEW, verdict="wrong",
                  sendable="must_not_send")  # fmt: skip
        await add(s, ctx, conv, intent="price_quote", config=NEW, status="unanswered")
        for _ in range(3):
            await add(s, ctx, conv, intent="faq", config=NEW)

    body = (await client.get("/api/v1/shadow/summary?days=14")).json()
    assert body["judge_calibrated"] is True
    price = next(i for i in body["intents"] if i["intent"] == "price_quote")
    assert (price["total"], price["scored"], price["unanswered"]) == (51, 50, 1)
    assert price["sendable_rate"] == 0.98 and price["wrong_rate"] == 0.02
    assert price["ready"] is True and price["models"] == ["claude-sonnet-5"]
    assert price["median_bot_lead_s"] == 120
    faq = next(i for i in body["intents"] if i["intent"] == "faq")
    assert faq["ready"] is False and faq["not_ready_because"] == [
        "chưa đủ mẫu (3/30 cặp được chấm)"
    ]

    # Một lần báo giá mâu thuẫn (câu nhân viên không bị nghi) ⇒ chưa đề xuất trong 7 ngày.
    async with session_factory() as s, s.begin():
        await add(s, ctx, conv, intent="price_quote", config=NEW, verdict="wrong",
                  sendable="must_not_send",
                  checks={"amounts": "conflict", "reference_suspect": False})  # fmt: skip
    price = next(
        i
        for i in (await client.get("/api/v1/shadow/summary")).json()["intents"]
        if i["intent"] == "price_quote"
    )
    assert not price["ready"]
    assert "1 lần báo giá mâu thuẫn trong 7 ngày" in price["not_ready_because"]


async def test_blind_window_is_anchored_at_the_last_blind_pair(app_client, session_factory):
    client, ctx, _, conn = await logged_in(
        lambda: app_client("https://api.test"), session_factory, role="owner"
    )
    async with session_factory() as s, s.begin():
        conv = await make_conversation(s, ctx, await make_customer(s, ctx), conn)
        # Shop rời Shadow 20 ngày trước, sau 10 ngày đo đủ 30 cặp mù (nay đã ở Learning).
        left_shadow = datetime.now(UTC) - timedelta(days=20)
        for i in range(30):
            await add(s, ctx, conv, intent="faq", config=NEW, label="sendable",
                      when=left_shadow - timedelta(hours=i * 8))  # fmt: skip

    body = (await client.get("/api/v1/shadow/summary?days=14")).json()
    faq = next(i for i in body["intents"] if i["intent"] == "faq")
    assert faq["scored"] == 30 and faq["ready"] is True


async def test_blind_pairs_older_than_60_days_expire(app_client, session_factory):
    client, ctx, _, conn = await logged_in(
        lambda: app_client("https://api.test"), session_factory, role="owner"
    )
    async with session_factory() as s, s.begin():
        conv = await make_conversation(s, ctx, await make_customer(s, ctx), conn)
        for i in range(30):
            await add(s, ctx, conv, intent="faq", config=NEW, label="sendable",
                      when=datetime.now(UTC) - timedelta(days=70, hours=i))  # fmt: skip
    body = (await client.get("/api/v1/shadow/summary?days=14")).json()
    assert all(i["intent"] != "faq" for i in body["intents"])
