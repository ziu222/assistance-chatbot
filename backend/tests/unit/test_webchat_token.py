import base64
import json
import time
from uuid import uuid4

import jwt
import pytest

from app.modules.integration.infrastructure.webchat.token import (
    InvalidToken,
    TokenSigner,
)

SECRET = "s" * 32
signer = TokenSigner({"k1": SECRET}, active_kid="k1", ttl_seconds=3600)


def _claims():
    return {"tid": str(uuid4()), "cid": str(uuid4()), "vid": str(uuid4())}


def test_roundtrip():
    c = _claims()
    token, expires_at = signer.issue(c["tid"], c["cid"], c["vid"])
    v = signer.verify(token)
    assert (str(v.tenant_id), str(v.connection_id), v.visitor_id) == (c["tid"], c["cid"], c["vid"])
    assert v.expires_at == expires_at


def test_rotated_old_key_still_verifies():
    c = _claims()
    old, _ = TokenSigner({"k0": "o" * 32}, active_kid="k0", ttl_seconds=60).issue(**_kw(c))
    rotated = TokenSigner({"k0": "o" * 32, "k1": SECRET}, active_kid="k1", ttl_seconds=60)
    assert rotated.verify(old).visitor_id == c["vid"]


def _kw(c):
    return {"tenant_id": c["tid"], "connection_id": c["cid"], "visitor_id": c["vid"]}


def _forge(payload, *, key=SECRET, alg="HS256", kid="k1"):
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid})


def _valid_payload(**over):
    now = int(time.time())
    return {**_claims(), "iat": now, "exp": now + 60, **over}


@pytest.mark.parametrize(
    "token",
    [
        _forge(_valid_payload(exp=int(time.time()) - 1)),  # hết hạn
        _forge(_valid_payload(), key="x" * 32),  # sai chữ ký
        _forge(_valid_payload(), kid="k9"),  # kid lạ
        _forge({k: v for k, v in _valid_payload().items() if k != "exp"}),  # thiếu exp
        _forge(_valid_payload(vid="")),  # vid rỗng
        _forge(_valid_payload(tid="not-a-uuid")),
        _forge(_valid_payload(), alg="HS512", key="s" * 64),  # alg khác HS256
        "not.a.token",
        "",
    ],
)
def test_rejects_invalid(token):
    with pytest.raises(InvalidToken):
        signer.verify(token)


def test_rejects_alg_none():
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    token = f"{b64({'alg': 'none', 'kid': 'k1'})}.{b64(_valid_payload())}."
    with pytest.raises(InvalidToken):
        signer.verify(token)


def test_rejects_short_secret():
    with pytest.raises(ValueError):
        TokenSigner({"k1": "short"}, active_kid="k1", ttl_seconds=60)


def test_rejects_unknown_active_kid():
    with pytest.raises(ValueError):
        TokenSigner({"k1": SECRET}, active_kid="k2", ttl_seconds=60)
