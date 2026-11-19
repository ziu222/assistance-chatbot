"""Session token của khách webchat: JWT HS256, thuật toán cố định, xoay khóa bằng `kid`."""

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import jwt

ALGORITHM = "HS256"
MIN_SECRET_BYTES = 32
REQUIRED_CLAIMS = ["tid", "cid", "vid", "iat", "exp"]


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class VisitorClaims:
    tenant_id: UUID
    connection_id: UUID
    visitor_id: str
    expires_at: datetime


class TokenSigner:
    def __init__(self, secrets: dict[str, str], *, active_kid: str, ttl_seconds: int) -> None:
        if active_kid not in secrets:
            raise ValueError("webchat token: active kid has no secret")
        if any(len(s.encode()) < MIN_SECRET_BYTES for s in secrets.values()):
            raise ValueError(f"webchat token: secrets must be >= {MIN_SECRET_BYTES} bytes")
        self._secrets = dict(secrets)
        self._kid = active_kid
        self._ttl = ttl_seconds

    def issue(
        self, tenant_id: UUID | str, connection_id: UUID | str, visitor_id: str
    ) -> tuple[str, datetime]:
        now = int(time.time())
        exp = now + self._ttl
        payload = {
            "tid": str(tenant_id),
            "cid": str(connection_id),
            "vid": visitor_id,
            "iat": now,
            "exp": exp,
        }
        token = jwt.encode(
            payload, self._secrets[self._kid], algorithm=ALGORITHM, headers={"kid": self._kid}
        )
        return token, datetime.fromtimestamp(exp, UTC)

    def verify(self, token: str) -> VisitorClaims:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            secret = self._secrets.get(kid) if isinstance(kid, str) else None
            if secret is None:
                raise InvalidToken("unknown kid")
            # algorithms cố định: không bao giờ tin "alg" trong header (chặn alg=none/nhầm khóa).
            claims = jwt.decode(
                token, secret, algorithms=[ALGORITHM], options={"require": REQUIRED_CLAIMS}
            )
            if not isinstance(claims["vid"], str) or not claims["vid"]:
                raise InvalidToken("bad vid")
            return VisitorClaims(
                UUID(claims["tid"]),
                UUID(claims["cid"]),
                claims["vid"],
                datetime.fromtimestamp(claims["exp"], UTC),
            )
        except InvalidToken:
            raise
        except (jwt.PyJWTError, ValueError, TypeError, AttributeError) as exc:
            raise InvalidToken(type(exc).__name__) from exc
