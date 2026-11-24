"""Gán nhãn cặp đã chấm và hiệu chỉnh giám khảo (thiết kế 04 S3)."""

from .test_shadow_judge import drain, paired, verdict, worker
from .test_staff_api import CSRF, logged_in, login, staff


async def judged_shop(app_client, session_factory, role="owner"):
    """Shop có một cặp đã được giám khảo chấm 'gửi được', và một nhân viên đã đăng nhập."""
    w = await paired(session_factory)
    judge, _ = worker(session_factory, [verdict()] * 30)
    await drain(judge)
    _, _, email, _ = await staff(session_factory, role=role, ctx=w.ctx)
    client = await app_client("https://api.test")
    assert (await login(client, email)).status_code == 200
    return client


async def test_owner_sees_the_pair_labels_it_and_calibration_counts_it(app_client, session_factory):
    client = await judged_shop(app_client, session_factory)
    [item] = (await client.get("/api/v1/shadow/comparisons")).json()["items"]
    assert item["customer"] == "áo khoác gió giá bao nhiêu"
    assert "350" in item["draft"] and item["staff"] == "Dạ áo khoác gió 350k nha chị"
    assert item["judge"]["label"] == "sendable" and item["label"] is None

    r = await client.post(
        f"/api/v1/shadow/comparisons/{item['id']}/label", json={"label": "rewrite"}, headers=CSRF
    )
    assert r.json() == {"ok": True}
    assert (await client.get("/api/v1/shadow/comparisons")).json()["items"] == []  # đã gán
    again = (await client.get("/api/v1/shadow/comparisons?unlabelled=false")).json()["items"]
    assert again[0]["label"] == "rewrite"

    cal = (await client.get("/api/v1/shadow/calibration")).json()
    assert (cal["labels"], cal["agreement"], cal["calibrated"]) == (1, 0.0, False)
    assert cal["needs"] == {"labels": 30, "kappa": 0.6}

    bad = await client.post(
        f"/api/v1/shadow/comparisons/{item['id']}/label", json={"label": "ok"}, headers=CSRF
    )
    assert bad.status_code == 422


async def test_agents_cannot_label_and_other_tenants_cannot_see(app_client, session_factory):
    agent = await judged_shop(app_client, session_factory, role="agent")
    assert (await agent.get("/api/v1/shadow/comparisons")).status_code == 403
    assert (await agent.get("/api/v1/shadow/calibration")).status_code == 200

    owner = await judged_shop(app_client, session_factory)
    [item] = (await owner.get("/api/v1/shadow/comparisons")).json()["items"]
    other, *_ = await logged_in(
        lambda: app_client("https://api.test"), session_factory, role="owner"
    )
    assert (await other.get("/api/v1/shadow/comparisons")).json()["items"] == []
    r = await other.post(
        f"/api/v1/shadow/comparisons/{item['id']}/label", json={"label": "wrong"}, headers=CSRF
    )
    assert r.status_code == 404
