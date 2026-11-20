"""Tải khói (không phải benchmark): N visitor song song, mỗi visitor gửi M tin qua API public.

Đo latency POST /messages và độ trễ outbox -> publish (từ outbox_events). Cần API + worker đang
chạy và một public_key có allowlist bất kỳ.

  uv run python scripts/smoke_load.py --key wpk_xxx --visitors 20 --messages 10

Lưu ý: rate limit thật vẫn bật (20 tin/phút/visitor, 120/phút/IP) — giữ visitors*messages
trong giới hạn hoặc chạy trên môi trường đã nới giới hạn.
"""

import argparse
import asyncio
import statistics
import time
import uuid

import httpx
from sqlalchemy import text

from app.config.settings import get_settings
from app.shared.infrastructure.db import create_engine


async def visitor(client: httpx.AsyncClient, key: str, n: int, origin: str) -> list[float]:
    r = await client.post(
        "/public/webchat/v1/sessions", json={"public_key": key}, headers={"Origin": origin}
    )
    r.raise_for_status()
    auth = {"Authorization": f"Bearer {r.json()['token']}"}
    latencies = []
    for i in range(n):
        start = time.perf_counter()
        r = await client.post(
            "/public/webchat/v1/messages",
            headers=auth,
            json={"client_message_id": str(uuid.uuid4()), "text": f"smoke {i}"},
        )
        latencies.append(time.perf_counter() - start)
        r.raise_for_status()
    return latencies


def pct(values: list[float], p: float) -> float:
    return statistics.quantiles(values, n=100)[int(p) - 1] if len(values) > 1 else values[0]


async def main(args: argparse.Namespace) -> None:
    settings = get_settings()
    started = time.time()
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as client:
        results = await asyncio.gather(
            *(
                visitor(client, args.key, args.messages, settings.public_origin)
                for _ in range(args.visitors)
            )
        )
    latencies = [x * 1000 for r in results for x in r]
    await asyncio.sleep(2)  # cho relay publish nốt

    engine = create_engine(settings.database_url)
    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT extract(epoch FROM published_at - created_at) * 1000 "
                        "FROM outbox_events "
                        "WHERE event_type = 'message.created' AND created_at >= to_timestamp(:t) "
                        "AND published_at IS NOT NULL"
                    ),
                    {"t": started},
                )
            )
            .scalars()
            .all()
        )
    await engine.dispose()
    lags = [float(x) for x in rows]

    print(f"messages: {len(latencies)}  visitors: {args.visitors}")
    print(
        f"POST /messages ms  p50={pct(latencies, 50):.1f}  p95={pct(latencies, 95):.1f}  "
        f"max={max(latencies):.1f}"
    )
    if lags:
        print(
            f"outbox->publish ms p50={pct(lags, 50):.1f}  p95={pct(lags, 95):.1f}  "
            f"max={max(lags):.1f}  (n={len(lags)})"
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--key", required=True)
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--visitors", type=int, default=10)
    p.add_argument("--messages", type=int, default=10)
    asyncio.run(main(p.parse_args()))
