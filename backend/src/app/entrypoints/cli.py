"""CLI quản trị: `uv run python -m app.entrypoints.cli seed --tenant "Shop A" --email a@shop.vn
--origin https://shop.vn`. Mật khẩu ngẫu nhiên in ra MỘT lần, không lưu ở đâu khác."""

import argparse
import asyncio
import base64
import json
import re
import secrets
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.modules.identity.infrastructure.passwords import hash_password
from app.modules.integration.domain.origin import valid_origins
from app.shared.infrastructure.db import create_engine, create_session_factory


@dataclass(frozen=True)
class SeedResult:
    tenant_id: UUID
    public_key: str
    password: str


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "tenant"
    return f"{base[:40]}-{secrets.token_hex(3)}"


async def seed(s: AsyncSession, *, tenant_name: str, email: str, origins: list[str]) -> SeedResult:
    tenant_id, user_id = uuid4(), uuid4()
    password = secrets.token_urlsafe(18)  # 24 ký tự
    # 'wpk_' + 24 ký tự base32 ngẫu nhiên (thiết kế §5)
    public_key = "wpk_" + base64.b32encode(secrets.token_bytes(15)).decode().lower()
    await s.execute(
        text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"),
        {"id": tenant_id, "n": tenant_name, "s": _slug(tenant_name)},
    )
    await s.execute(
        text("INSERT INTO users (id, email, password_hash, display_name) VALUES (:id, :e, :h, :d)"),
        {"id": user_id, "e": email, "h": hash_password(password), "d": email.split("@")[0]},
    )
    await s.execute(
        text("INSERT INTO tenant_memberships (tenant_id, user_id, role) VALUES (:t, :u, 'owner')"),
        {"t": tenant_id, "u": user_id},
    )
    await s.execute(
        text(
            "INSERT INTO channel_connections (id, tenant_id, provider, public_key, config) "
            "VALUES (:id, :t, 'webchat', :k, CAST(:cfg AS jsonb))"
        ),
        {
            "id": uuid4(),
            "t": tenant_id,
            "k": public_key,
            "cfg": json.dumps({"allowed_origins": valid_origins(origins), "greeting": "Xin chào!"}),
        },
    )
    return SeedResult(tenant_id, public_key, password)


async def _main(args: argparse.Namespace) -> None:
    engine = create_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as s:
            result = await seed(s, tenant_name=args.tenant, email=args.email, origins=args.origin)
            await s.commit()
    finally:
        await engine.dispose()
    print(f"tenant_id : {result.tenant_id}")
    print(f"public_key: {result.public_key}")
    print(f"email     : {args.email}")
    print(f"password  : {result.password}   <- chỉ hiện một lần, hãy lưu lại")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="app.entrypoints.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("seed", help="tạo tenant + owner + widget webchat")
    p.add_argument("--tenant", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--origin", action="append", default=[], help="origin được nhúng widget")
    asyncio.run(_main(parser.parse_args()))
