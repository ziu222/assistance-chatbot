"""Kiểm thử API quản lý kết nối kênh `/api/v1/channels` (O5 pha 2 PR 1)."""

import hashlib
import hmac
import json
import logging

from cryptography.fernet import Fernet
from sqlalchemy import text

from app.modules.integration.domain.channels import verify_token_hash
from app.shared.infrastructure.crypto import decrypt_json

from .test_staff_api import CSRF, login, staff

TEST_CRED_KEY = Fernet.generate_key().decode("ascii")
PAGE_ID = "100200300400"
PAT_1 = "EAAB-page-access-token-001"
SECRET_1 = "meta-app-secret-001"
VERIFY_1 = "meta-verify-token-001"


async def _login_staff(
    app_client,
    session_factory,
    *,
    role: str = "owner",
    credentials_key: str = TEST_CRED_KEY,
):
    client = await app_client("https://api.test", channel_credentials_key=credentials_key)
    ctx, uid, email, webchat_conn_id = await staff(session_factory, role=role)
    r = await login(client, email)
    assert r.status_code == 200, r.text
    return client, ctx, uid, webchat_conn_id


async def _create_messenger(client, **over):
    body = {
        "page_id": PAGE_ID,
        "page_access_token": PAT_1,
        "app_secret": SECRET_1,
        "verify_token": VERIFY_1,
    } | over
    return await client.post("/api/v1/channels/messenger", json=body, headers=CSRF)


def _sign(raw_body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def test_requires_auth_and_csrf(app_client, session_factory):
    anon = await app_client("https://api.test", channel_credentials_key=TEST_CRED_KEY)
    assert (await anon.get("/api/v1/channels")).status_code == 401
    assert (
        await anon.post(
            "/api/v1/channels/messenger",
            json={
                "page_id": PAGE_ID,
                "page_access_token": PAT_1,
                "app_secret": SECRET_1,
                "verify_token": VERIFY_1,
            },
            headers=CSRF,
        )
    ).status_code == 401

    owner_client, _, _, webchat_id = await _login_staff(app_client, session_factory, role="owner")
    no_csrf_post = await owner_client.post(
        "/api/v1/channels/messenger",
        json={
            "page_id": PAGE_ID,
            "page_access_token": PAT_1,
            "app_secret": SECRET_1,
            "verify_token": VERIFY_1,
        },
    )
    assert no_csrf_post.status_code == 403
    assert no_csrf_post.json()["error"]["code"] == "csrf_rejected"

    no_csrf_patch = await owner_client.patch(
        f"/api/v1/channels/{webchat_id}", json={"status": "disabled"}
    )
    assert no_csrf_patch.status_code == 403
    assert no_csrf_patch.json()["error"]["code"] == "csrf_rejected"


async def test_agent_role_forbidden_on_all_channels_endpoints(app_client, session_factory):
    agent_client, _, _, webchat_id = await _login_staff(app_client, session_factory, role="agent")

    r_list = await agent_client.get("/api/v1/channels")
    assert r_list.status_code == 403
    assert r_list.json()["error"]["code"] == "forbidden"

    r_create = await _create_messenger(agent_client)
    assert r_create.status_code == 403
    assert r_create.json()["error"]["code"] == "forbidden"

    r_patch = await agent_client.patch(
        f"/api/v1/channels/{webchat_id}", json={"status": "disabled"}, headers=CSRF
    )
    assert r_patch.status_code == 403
    assert r_patch.json()["error"]["code"] == "forbidden"


async def test_list_channels_tenant_isolation_and_no_secret_leak(app_client, session_factory):
    client_a, ctx_a, _, webchat_a = await _login_staff(app_client, session_factory, role="admin")
    client_b, ctx_b, _, webchat_b = await _login_staff(app_client, session_factory, role="owner")

    created = await _create_messenger(client_a)
    assert created.status_code == 201, created.text
    other_created = await _create_messenger(client_b, page_id="999888777")
    assert other_created.status_code == 201, other_created.text

    res_a = await client_a.get("/api/v1/channels")
    assert res_a.status_code == 200
    body_a = res_a.json()
    assert body_a["credentials_configured"] is True

    items_a = body_a["items"]
    assert [item["id"] for item in items_a] == [str(webchat_a), created.json()["id"]]
    assert all(item["id"] != str(webchat_b) for item in items_a)
    assert all(item["id"] != other_created.json()["id"] for item in items_a)

    webchat_item, msg_item = items_a
    assert webchat_item["provider"] == "webchat"
    assert webchat_item["public_key"].startswith("wpk_")
    assert webchat_item["has_credentials"] is False
    assert webchat_item["webhook_url"] is None

    assert msg_item["provider"] == "messenger"
    assert msg_item["external_account_id"] == PAGE_ID
    assert msg_item["public_key"] is None
    assert msg_item["has_credentials"] is True
    assert msg_item["webhook_url"] == "http://widget.test/webhooks/messenger"

    # Tuyệt đối không có token/secret nào xuất hiện trong payload JSON.
    raw_text = res_a.text
    for secret in (PAT_1, SECRET_1, VERIFY_1, TEST_CRED_KEY):
        assert secret not in raw_text
    assert "credentials_encrypted" not in raw_text


async def test_create_messenger_channel_encrypts_and_works_with_webhook(
    app_client, session_factory, caplog
):
    client, ctx, _, _ = await _login_staff(app_client, session_factory, role="owner")

    with caplog.at_level(logging.INFO):
        r = await _create_messenger(client, page_id=f"  {PAGE_ID}  ")
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["provider"] == "messenger"
    assert data["external_account_id"] == PAGE_ID
    assert data["status"] == "active"
    assert data["public_key"] is None
    assert data["has_credentials"] is True
    assert data["webhook_url"] == "http://widget.test/webhooks/messenger"
    for secret in (PAT_1, SECRET_1, VERIFY_1, TEST_CRED_KEY):
        assert secret not in r.text
        assert secret not in caplog.text

    # Xác minh DB lưu bản mã hóa và config.verify_token_hash khớp.
    async with session_factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT config, credentials_encrypted FROM channel_connections "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {"t": ctx.tenant_id, "id": data["id"]},
            )
        ).one()
    assert row.config["verify_token_hash"] == verify_token_hash(VERIFY_1)
    for secret in (PAT_1, SECRET_1, VERIFY_1):
        assert secret not in row.credentials_encrypted
    assert decrypt_json(row.credentials_encrypted, TEST_CRED_KEY) == {
        "page_access_token": PAT_1,
        "app_secret": SECRET_1,
        "verify_token": VERIFY_1,
    }

    # Xác minh thực tế GET /webhooks/messenger và POST /webhooks/messenger dùng được ngay.
    verify_res = await client.get(
        "/webhooks/messenger",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_1,
            "hub.challenge": "CHALLENGE_OK",
        },
    )
    assert verify_res.status_code == 200
    assert verify_res.text == "CHALLENGE_OK"

    raw_hook = json.dumps(
        {
            "object": "page",
            "entry": [
                {
                    "id": PAGE_ID,
                    "time": 1_732_680_000_000,
                    "messaging": [
                        {
                            "sender": {"id": "psid_1"},
                            "recipient": {"id": PAGE_ID},
                            "timestamp": 1_732_680_000_000,
                            "message": {"mid": "mid_1", "text": "Chào shop"},
                        }
                    ],
                }
            ],
        }
    ).encode("utf-8")
    post_hook = await client.post(
        "/webhooks/messenger",
        content=raw_hook,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(raw_hook, SECRET_1),
        },
    )
    assert post_hook.status_code == 200
    assert post_hook.json() == {"ok": True, "received": 1}


async def test_duplicate_active_page_id_returns_409(app_client, session_factory):
    client_a, *_ = await _login_staff(app_client, session_factory, role="owner")
    client_b, *_ = await _login_staff(app_client, session_factory, role="owner")

    assert (await _create_messenger(client_a)).status_code == 201

    dup_same = await _create_messenger(client_a)
    assert dup_same.status_code == 409
    assert dup_same.json()["error"]["code"] == "channel_exists"

    dup_other_tenant = await _create_messenger(client_b)
    assert dup_other_tenant.status_code == 409
    assert dup_other_tenant.json()["error"]["code"] == "channel_exists"


async def test_missing_credentials_key_returns_503_and_reports_unconfigured(
    app_client, session_factory
):
    client, *_ = await _login_staff(app_client, session_factory, role="owner", credentials_key="")

    listed = await client.get("/api/v1/channels")
    assert listed.status_code == 200
    assert listed.json()["credentials_configured"] is False

    created = await _create_messenger(client)
    assert created.status_code == 503
    assert created.json()["error"]["code"] == "credentials_key_missing"


async def test_create_validation_rejects_bad_page_id_or_short_secret(app_client, session_factory):
    client, *_ = await _login_staff(app_client, session_factory, role="owner")

    bad_page = await _create_messenger(client, page_id="100 200")
    assert bad_page.status_code == 422
    assert bad_page.json()["error"]["code"] == "invalid_request"

    short_secret = await _create_messenger(client, app_secret="short7")
    assert short_secret.status_code == 422
    assert short_secret.json()["error"]["code"] == "invalid_request"
    assert "short7" not in short_secret.text


async def test_patch_rotate_partial_credentials_toggle_status_and_isolation(
    app_client, session_factory, caplog
):
    client_a, ctx_a, _, webchat_a = await _login_staff(app_client, session_factory, role="owner")
    client_b, *_ = await _login_staff(app_client, session_factory, role="owner")

    created = (await _create_messenger(client_a)).json()
    conn_id = created["id"]

    # 1. Xoay vòng riêng page_access_token ⇒ giữ nguyên app_secret & verify_token.
    new_pat = "EAAB-rotated-page-token-002"
    with caplog.at_level(logging.INFO):
        rot_pat = await client_a.patch(
            f"/api/v1/channels/{conn_id}",
            json={"page_access_token": new_pat},
            headers=CSRF,
        )
    assert rot_pat.status_code == 200, rot_pat.text
    assert rot_pat.json()["has_credentials"] is True
    assert new_pat not in rot_pat.text
    assert new_pat not in caplog.text

    async with session_factory() as s:
        enc = (
            await s.execute(
                text(
                    "SELECT credentials_encrypted FROM channel_connections "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {"t": ctx_a.tenant_id, "id": conn_id},
            )
        ).scalar_one()
    assert decrypt_json(enc, TEST_CRED_KEY) == {
        "page_access_token": new_pat,
        "app_secret": SECRET_1,
        "verify_token": VERIFY_1,
    }

    # Verify token cũ vẫn hoạt động vì không đổi verify_token.
    still_ok = await client_a.get(
        "/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY_1, "hub.challenge": "C1"},
    )
    assert still_ok.status_code == 200

    # 2. Xoay vòng verify_token ⇒ cập nhật config.verify_token_hash, token cũ hết hiệu lực.
    new_verify = "meta-verify-token-rotated-002"
    rot_verify = await client_a.patch(
        f"/api/v1/channels/{conn_id}",
        json={"verify_token": new_verify},
        headers=CSRF,
    )
    assert rot_verify.status_code == 200
    assert new_verify not in rot_verify.text

    old_fail = await client_a.get(
        "/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY_1, "hub.challenge": "C2"},
    )
    assert old_fail.status_code == 403
    new_ok = await client_a.get(
        "/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": new_verify, "hub.challenge": "C2"},
    )
    assert new_ok.status_code == 200
    assert new_ok.text == "C2"

    # 3. Tắt rồi bật lại kết nối.
    disabled = await client_a.patch(
        f"/api/v1/channels/{conn_id}", json={"status": "disabled"}, headers=CSRF
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    disabled_hook = await client_a.get(
        "/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": new_verify, "hub.challenge": "C3"},
    )
    assert disabled_hook.status_code == 403

    # Khi conn_id đang tắt, tenant B có thể chiếm page_id; nếu tenant A bật lại ⇒ 409.
    taken_by_b = await _create_messenger(client_b)
    assert taken_by_b.status_code == 201
    reactivate_conflict = await client_a.patch(
        f"/api/v1/channels/{conn_id}", json={"status": "active"}, headers=CSRF
    )
    assert reactivate_conflict.status_code == 409
    assert reactivate_conflict.json()["error"]["code"] == "channel_exists"

    # Tắt kết nối của tenant B rồi tenant A bật lại thành công.
    assert (
        await client_b.patch(
            f"/api/v1/channels/{taken_by_b.json()['id']}",
            json={"status": "disabled"},
            headers=CSRF,
        )
    ).status_code == 200
    reactivated = await client_a.patch(
        f"/api/v1/channels/{conn_id}", json={"status": "active"}, headers=CSRF
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "active"

    # 4. Xoay vòng credentials trên kết nối không phải messenger (webchat) ⇒ 422 invalid_request.
    webchat_rot = await client_a.patch(
        f"/api/v1/channels/{webchat_a}",
        json={"page_access_token": "EAAB-invalid-for-webchat"},
        headers=CSRF,
    )
    assert webchat_rot.status_code == 422
    assert webchat_rot.json()["error"]["code"] == "invalid_request"

    # 5. PATCH rỗng ⇒ 422 invalid_request; PATCH kết nối của tenant khác ⇒ 404 not_found.
    empty_patch = await client_a.patch(f"/api/v1/channels/{conn_id}", json={}, headers=CSRF)
    assert empty_patch.status_code == 422
    assert empty_patch.json()["error"]["code"] == "invalid_request"

    cross_tenant = await client_b.patch(
        f"/api/v1/channels/{conn_id}", json={"status": "disabled"}, headers=CSRF
    )
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["error"]["code"] == "not_found"


async def test_patch_missing_credentials_key_blocks_rotation_but_allows_status_toggle(
    app_client, session_factory
):
    client_with_key, *_ = await _login_staff(app_client, session_factory, role="owner")
    created = (await _create_messenger(client_with_key)).json()
    conn_id = created["id"]

    # Dùng lại cookie phiên hiện tại trên app client chưa cấu hình CHANNEL_CREDENTIALS_KEY.
    client_no_key = await app_client("https://api.test", channel_credentials_key="")
    client_no_key.cookies.update(client_with_key.cookies)

    rot_missing = await client_no_key.patch(
        f"/api/v1/channels/{conn_id}",
        json={"page_access_token": "EAAB-new-token-without-key"},
        headers=CSRF,
    )
    assert rot_missing.status_code == 503
    assert rot_missing.json()["error"]["code"] == "credentials_key_missing"

    toggle_ok = await client_no_key.patch(
        f"/api/v1/channels/{conn_id}",
        json={"status": "disabled"},
        headers=CSRF,
    )
    assert toggle_ok.status_code == 200
    assert toggle_ok.json()["status"] == "disabled"


async def test_patch_rotate_app_secret_updates_webhook_hmac(app_client, session_factory):
    client, *_ = await _login_staff(app_client, session_factory, role="owner")
    conn_id = (await _create_messenger(client)).json()["id"]

    new_secret = "meta-app-secret-rotated-002"
    rot = await client.patch(
        f"/api/v1/channels/{conn_id}",
        json={"app_secret": new_secret},
        headers=CSRF,
    )
    assert rot.status_code == 200
    assert new_secret not in rot.text

    raw_hook = json.dumps(
        {
            "object": "page",
            "entry": [
                {
                    "id": PAGE_ID,
                    "time": 1_732_680_000_000,
                    "messaging": [
                        {
                            "sender": {"id": "psid_2"},
                            "recipient": {"id": PAGE_ID},
                            "timestamp": 1_732_680_000_000,
                            "message": {"mid": "mid_2", "text": "Alo shop"},
                        }
                    ],
                }
            ],
        }
    ).encode("utf-8")

    old_sig_res = await client.post(
        "/webhooks/messenger",
        content=raw_hook,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(raw_hook, SECRET_1),
        },
    )
    assert old_sig_res.status_code == 403

    new_sig_res = await client.post(
        "/webhooks/messenger",
        content=raw_hook,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(raw_hook, new_secret),
        },
    )
    assert new_sig_res.status_code == 200
    assert new_sig_res.json() == {"ok": True, "received": 1}
