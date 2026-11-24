"""So sánh shadow S1 (thiết kế 04): ghép nháp với câu nhân viên, chấm bằng code."""

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


async def drafted(session_factory):
    """Shop + một nháp báo giá áo khoác gió 350.000đ (LLM giả)."""
    w = await make_world(session_factory)
    runner, _ = make_runner(session_factory, [classify("price_quote"), search(), quote_draft()])
    req = await request_for(session_factory, w)
    await runner.handle(req)
    return w, req.conversation_id


async def staff_says(session_factory, w, conversation_id, content, at):
    async with session_factory() as s, s.begin():
        seq = (
            await s.execute(
                text(
                    "UPDATE conversations SET last_sequence = last_sequence + 1 "
                    "WHERE id = :c RETURNING last_sequence"
                ),
                {"c": conversation_id},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO messages (id, tenant_id, channel_connection_id, conversation_id, "
                "external_message_id, direction, sender_type, content, status, sequence, "
                "occurred_at) VALUES (:id, :t, :co, :c, :ext, 'outbound', 'human', :x, 'sent', "
                ":seq, :at)"
            ),
            {
                "id": uuid4(),
                "t": w.tenant_id,
                "co": w.conn,
                "c": conversation_id,
                "ext": str(uuid4()),
                "x": content,
                "seq": seq,
                "at": at,
            },
        )


async def comparison(session_factory, w):
    async with session_factory() as s:
        return (
            (
                await s.execute(
                    text("SELECT * FROM shadow_comparisons WHERE tenant_id = :t"),
                    {"t": w.tenant_id},
                )
            )
            .mappings()
            .all()
        )


async def test_staff_reply_with_the_same_price_is_paired_for_the_judge(session_factory):
    w, conv = await drafted(session_factory)
    now = datetime.now(UTC)
    await staff_says(session_factory, w, conv, "Dạ áo khoác gió 350k nha chị", now)

    await compare_due(session_factory, now=now + timedelta(minutes=1))
    assert await comparison(session_factory, w) == []  # nhân viên có thể còn gõ tiếp

    await compare_due(session_factory, now=now + timedelta(minutes=5))
    [c] = await comparison(session_factory, w)
    assert (c["status"], c["draft_exposure"], c["intent"]) == ("paired", "hidden", "price_quote")
    assert c["code_checks"]["amounts"] == "agree" and c["code_verdict"] is None
    assert c["code_checks"]["products"] == "match"
    assert c["config_version"]["template"] == "faq_sales_quote" and c["config_version"]["model"]
    assert c["reference_from_seq"] == c["reference_to_seq"]
    assert c["bot_lead_seconds"] is not None

    await compare_due(session_factory, now=now + timedelta(minutes=6))
    assert len(await comparison(session_factory, w)) == 1  # chạy lại không chấm trùng


async def test_conflicting_known_price_is_wrong(session_factory):
    w, conv = await drafted(session_factory)
    now = datetime.now(UTC)
    async with session_factory() as s, s.begin():  # 420k là giá có thật của một món khác
        await s.execute(
            text(
                "INSERT INTO catalog_items (id, tenant_id, sku, name, price_vnd, search_text, "
                "external_id) VALUES (:id, :t, 'Q-01', 'Quần jean nữ', 420000, "
                "'quan jean nu', 'Q-01')"
            ),
            {"id": uuid4(), "t": w.tenant_id},
        )
    await staff_says(session_factory, w, conv, "Dạ áo khoác gió giá 420.000đ ạ", now)
    await compare_due(session_factory, now=now + timedelta(minutes=5))
    [c] = await comparison(session_factory, w)
    assert c["code_verdict"] == "wrong" and c["code_checks"]["amounts"] == "conflict"


async def test_no_staff_reply_becomes_unanswered_after_a_day(session_factory):
    w, _ = await drafted(session_factory)
    now = datetime.now(UTC)
    await compare_due(session_factory, now=now + timedelta(hours=2))
    assert await comparison(session_factory, w) == []
    await compare_due(session_factory, now=now + timedelta(hours=25))
    [c] = await comparison(session_factory, w)
    assert c["status"] == "unanswered" and c["code_checks"] is None
