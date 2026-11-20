"""Quan sát (thiết kế §13): metric chính, log JSON có trace_id, không lộ nội dung tin/token/IP."""

import json
import logging
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import text

from app.shared.application.errors import RetryableConflict
from app.shared.application.retry import retry_conflicts

from .test_public_webchat import bearer, connection, open_session, send, token_for


def sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.fixture
def client(app_client):
    return app_client("http://widget.test")


async def test_inbound_counters_by_result(client, session_factory):
    c = await client
    _, _, key = await connection(session_factory)
    token = await token_for(c, key)
    before = {
        r: sample("inbound_messages_total", result=r, reason=reason)
        for r, reason in (("created", ""), ("duplicate", ""), ("rejected", "invalid_request"))
    }
    cmid = str(uuid4())
    await send(c, token, "a", cmid)
    await send(c, token, "a", cmid)
    await send(c, token, "   ")

    assert sample("inbound_messages_total", result="created", reason="") == before["created"] + 1
    assert (
        sample("inbound_messages_total", result="duplicate", reason="") == before["duplicate"] + 1
    )
    assert (
        sample("inbound_messages_total", result="rejected", reason="invalid_request")
        == before["rejected"] + 1
    )


async def test_rate_limited_counter(client, session_factory):
    c = await client
    _, _, key = await connection(session_factory)
    before = sample("rate_limited_total", scope="sessions_ip")
    for _ in range(11):
        await open_session(c, key)
    assert sample("rate_limited_total", scope="sessions_ip") == before + 1


async def test_metrics_endpoint_has_route_templates_and_outbox_backlog(client, session_factory):
    c = await client
    _, _, key = await connection(session_factory)
    token = await token_for(c, key)
    await send(c, token, "x")
    await c.get(f"/w/{key}")

    body = (await c.get("/metrics")).text
    assert 'route="/public/webchat/v1/messages"' in body
    assert 'route="/w/{public_key}"' in body  # nhãn là template, không phải key thật
    assert key not in body
    assert "outbox_unpublished_count 2.0" in body  # conversation.created + message.created
    assert "outbox_oldest_age_seconds" in body


async def test_db_retry_counter():
    before = sample("db_tx_retries_total")
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableConflict("deadlock")
        return "ok"

    assert await retry_conflicts(flaky) == "ok"
    assert sample("db_tx_retries_total") == before + 1


async def test_access_log_is_json_with_trace_and_no_secrets(client, session_factory, caplog):
    c = await client
    _, _, key = await connection(session_factory)
    token = await token_for(c, key)
    secret_text = "số thẻ 4111 1111 1111 1111"

    with caplog.at_level(logging.INFO, logger="app.access"):
        r = await c.post(
            "/public/webchat/v1/messages",
            json={"client_message_id": str(uuid4()), "text": secret_text},
            headers=bearer(token) | {"X-Trace-Id": "trace-from-client-123"},
        )
    assert r.headers["x-trace-id"] == "trace-from-client-123"

    from app.shared.infrastructure.logging import JsonFormatter

    lines = [JsonFormatter().format(rec) for rec in caplog.records if rec.name == "app.access"]
    assert lines, "phải có access log"
    entry = json.loads(lines[-1])
    assert entry["trace_id"] == "trace-from-client-123"
    assert entry["route"] == "/public/webchat/v1/messages"
    assert entry["status"] == 201
    joined = "\n".join(lines)
    assert secret_text not in joined and token not in joined and "127.0.0.1" not in joined

    async with session_factory() as s:
        stored = (await s.execute(text("SELECT trace_id FROM messages"))).scalar_one()
    assert stored == "trace-from-client-123"  # trace đi xuyên request -> message -> outbox


async def test_invalid_trace_header_is_replaced(client, session_factory):
    c = await client
    r = await c.get("/health", headers={"X-Trace-Id": "bad trace\nid"})
    assert r.headers["x-trace-id"] != "bad trace\nid"
    assert len(r.headers["x-trace-id"]) == 32


def test_configured_logging_accepts_explicit_trace_id(capsys):
    """Hồi quy: factory từng đặt 'trace_id' => extra trùng tên ném KeyError, mọi request 500."""
    from app.shared.infrastructure.logging import configure_logging, trace_id_var

    root = logging.getLogger()
    saved = (root.handlers[:], root.level, logging.getLogRecordFactory())
    try:
        configure_logging()
        ctx = trace_id_var.set("ctx-trace-0001")
        try:
            logging.getLogger("app.access").info("request", extra={"trace_id": "explicit-01"})
            logging.getLogger("app.other").info("no extra")
        finally:
            trace_id_var.reset(ctx)
        lines = [json.loads(x) for x in capsys.readouterr().err.strip().splitlines()]
    finally:
        root.handlers[:], _, _ = saved
        root.setLevel(saved[1])
        logging.setLogRecordFactory(saved[2])
    assert [line["trace_id"] for line in lines[-2:]] == ["explicit-01", "ctx-trace-0001"]
    assert "ctx_trace_id" not in lines[-1]


def test_configured_logging_silences_uvicorn_access_log():
    """uvicorn.access in IP thô và query string (§13.1): phải bị tắt khi chạy production."""
    from app.shared.infrastructure.logging import configure_logging

    root = logging.getLogger()
    access = logging.getLogger("uvicorn.access")
    saved = (root.handlers[:], root.level, logging.getLogRecordFactory(), access.disabled)
    try:
        configure_logging()
        assert access.disabled
        assert logging.getLogger("uvicorn.error").propagate
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        logging.setLogRecordFactory(saved[2])
        access.disabled = saved[3]
