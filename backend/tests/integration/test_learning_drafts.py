"""Chế độ Learning L1 (thiết kế 05): nháp trong ô soạn tin, nhân viên dùng/sửa/bỏ, ghi lại."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import text

from app.modules.shadow.infrastructure.comparisons import compare_due

from .test_agent_runtime import (
    classify,
    make_runner,
    make_world,
    quote_draft,
    request_for,
    search,
)
from .test_staff_api import CSRF, inbound, logged_in, login, staff

DRAFT = "Dạ áo khoác gió giá 350.000đ ạ"


async def shop(app_client, session_factory, *, mode="learning", sample_rate="1.00"):
    """Shop có agent ở `mode`, một nháp báo giá, và một nhân viên đã đăng nhập."""
    w = await make_world(session_factory, rate=sample_rate)
    async with session_factory() as s, s.begin():
        await s.execute(
            text("UPDATE agents SET mode = :m WHERE id = :a"), {"m": mode, "a": w.agent_id}
        )
    runner, _ = make_runner(session_factory, [classify("price_quote"), search(), quote_draft()])
    req = await request_for(session_factory, w)
    await runner.handle(req)
    _, _, email, _ = await staff(session_factory, role="agent", ctx=w.ctx)
    client = await app_client("https://api.test")
    assert (await login(client, email)).status_code == 200
    return w, req.conversation_id, client


async def send(client, conv, body, draft_id=None):
    payload = {"client_message_id": str(uuid4()), "text": body}
    if draft_id:
        payload["draft_id"] = draft_id
    r = await client.post(f"/api/v1/conversations/{conv}/messages", json=payload, headers=CSRF)
    assert r.status_code == 201, r.text
    return r.json()


async def uses(session_factory, conv):
    async with session_factory() as s:
        return (
            (
                await s.execute(
                    text("SELECT * FROM draft_uses WHERE conversation_id = :c"), {"c": conv}
                )
            )
            .mappings()
            .all()
        )


async def test_shadow_mode_never_shows_drafts(app_client, session_factory):
    _, conv, client = await shop(app_client, session_factory, mode="shadow")
    assert (await client.get(f"/api/v1/conversations/{conv}/draft")).json() == {"draft": None}


async def test_use_as_is_is_recorded_and_marks_the_pair_as_shown(app_client, session_factory):
    w, conv, client = await shop(app_client, session_factory, sample_rate="0.00")
    draft = (await client.get(f"/api/v1/conversations/{conv}/draft")).json()["draft"]
    # Learning bỏ qua lấy mẫu: nhân viên cần nháp cho mọi tin dù sample rate = 0.
    assert draft["text"] == DRAFT and draft["amounts"] == [350000]

    body = await send(client, conv, "Dạ áo khoác gió giá  350.000đ ạ", draft["id"])
    assert body["draft_action"] == "as_is"
    [u] = await uses(session_factory, conv)
    assert (u["action"], u["edit_ratio"], u["message_id"]) == ("as_is", 0.0, uuid_of(body))
    assert (await client.get(f"/api/v1/conversations/{conv}/draft")).json() == {"draft": None}

    await compare_due(session_factory, now=datetime.now(UTC) + timedelta(minutes=5))
    async with session_factory() as s:
        exposure, signal = (
            await s.execute(
                text(
                    "SELECT (SELECT draft_exposure FROM shadow_comparisons "
                    "WHERE conversation_id = :c), "
                    "(SELECT kind FROM learning_signals WHERE conversation_id = :c "
                    "AND kind LIKE 'draft_%')"
                ),
                {"c": conv},
            )
        ).one()
    assert (exposure, signal) == ("shown", "draft_used")


def uuid_of(body):
    from uuid import UUID

    return UUID(body["message_id"])


async def test_edit_records_what_changed(app_client, session_factory):
    _, conv, client = await shop(app_client, session_factory)
    draft = (await client.get(f"/api/v1/conversations/{conv}/draft")).json()["draft"]
    await send(client, conv, "Dạ áo khoác gió giá 320.000đ ạ, freeship nội thành", draft["id"])
    [u] = await uses(session_factory, conv)
    assert u["action"] == "edited" and u["edit_ratio"] > 0
    assert u["diff_features"]["amounts_added"] == [320000]
    assert u["diff_features"]["amounts_removed"] == [350000]


async def test_typing_another_reply_discards_only_a_shown_draft(app_client, session_factory):
    _, conv, client = await shop(app_client, session_factory)
    await send(client, conv, "Dạ chị đợi em xíu")  # nháp chưa từng hiện ⇒ không ghi gì
    assert await uses(session_factory, conv) == []

    _, conv2, client2 = await shop(app_client, session_factory)
    await client2.get(f"/api/v1/conversations/{conv2}/draft")  # nhân viên đã thấy nháp
    body = await send(client2, conv2, "Dạ để em kiểm tra lại ạ")
    assert body["draft_action"] == "discarded"
    [u] = await uses(session_factory, conv2)
    assert u["action"] == "discarded" and u["message_id"] is not None


async def test_discard_button_new_customer_message_and_isolation(app_client, session_factory):
    w, conv, client = await shop(app_client, session_factory)
    draft = (await client.get(f"/api/v1/conversations/{conv}/draft")).json()["draft"]

    # Khách nhắn thêm ⇒ nháp cũ không còn ứng với tin cuối, không hiện nữa.
    await inbound(session_factory, w.ctx, w.conn, "à cho em hỏi thêm màu xanh")
    assert (await client.get(f"/api/v1/conversations/{conv}/draft")).json() == {"draft": None}

    r = await client.post(f"/api/v1/conversations/{conv}/draft/{draft['id']}/discard", headers=CSRF)
    assert r.json() == {"action": "discarded"}
    again = await client.post(
        f"/api/v1/conversations/{conv}/draft/{draft['id']}/discard", headers=CSRF
    )
    assert again.status_code == 404

    other, *_ = await logged_in(
        lambda: app_client("https://api.test"), session_factory, role="owner"
    )
    assert (await other.get(f"/api/v1/conversations/{conv}/draft")).json() == {"draft": None}
    foreign = await other.post(
        f"/api/v1/conversations/{conv}/draft/{draft['id']}/discard", headers=CSRF
    )
    assert foreign.status_code == 404
