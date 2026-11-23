"""Eval runner bước 2.12: chạy bộ shop giả lập qua đúng pipeline thật rồi chấm điểm.

    uv run python backend/evals/run_eval.py --fake                       # không gọi mạng (CI/smoke)
    uv run python backend/evals/run_eval.py --per-category 4 --max-cost-usd 1 \\
        --out ../docs/evals/agent-eval.md                                 # gọi LLM thật

Mỗi lần chạy dựng lại một DB riêng (`<tên DB dev>_eval`, không đụng dữ liệu dev), nạp 12 shop
(catalog, FAQ, chính sách, cấu hình giờ), rồi với mỗi nhiệm vụ: tạo hội thoại có lịch sử, gọi
`PolicyStage` + `AgentRunner` y như agent worker, đọc kết quả từ DB và chấm bằng `scoring.py`.

Không dùng Message Batches API: vòng tool của agent là tuần tự (lượt sau phụ thuộc kết quả tool
lượt trước), nên không đóng gói thành một batch được; runner chạy song song có giới hạn.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[0] / "src"))
sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from scoring import DIMENSIONS, Outcome, as_json, rate, score, summary  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.config.settings import get_settings  # noqa: E402
from app.entrypoints.cli import seed  # noqa: E402
from app.modules.agent_management.infrastructure.repository import (  # noqa: E402
    insert_agent,
    upsert_ai_settings,
)
from app.modules.agent_management.infrastructure.templates import get_template  # noqa: E402
from app.modules.agent_runtime.infrastructure.run_requests import RunRequest  # noqa: E402
from app.modules.agent_runtime.llm.factory import build_llm  # noqa: E402
from app.modules.agent_runtime.llm.port import LLMRequest, LLMResult, Target, Usage  # noqa: E402
from app.modules.agent_runtime.prompts import PROMPT_VERSION  # noqa: E402
from app.modules.agent_runtime.runner import AgentRunner  # noqa: E402
from app.modules.agent_runtime.schemas import ClassifyOut, DraftOut  # noqa: E402
from app.modules.conversation.application.receive_inbound_message import (  # noqa: E402
    ReceiveInboundMessage,
)
from app.modules.conversation.infrastructure.unit_of_work import (  # noqa: E402
    SqlConversationUnitOfWork,
)
from app.modules.conversation_policy.domain.hours import BusinessHours  # noqa: E402
from app.modules.conversation_policy.domain.policy import (  # noqa: E402
    PolicyConfig,
    SituationConfig,
)
from app.modules.conversation_policy.infrastructure.repository import publish  # noqa: E402
from app.modules.conversation_policy.stage import PolicyStage  # noqa: E402
from app.modules.integration.domain.channel_event import NormalizedChannelEvent  # noqa: E402
from app.modules.knowledge.domain.catalog_csv import parse_catalog_csv  # noqa: E402
from app.modules.knowledge.infrastructure.catalog import import_catalog  # noqa: E402
from app.modules.knowledge.infrastructure.content import create_entry  # noqa: E402
from app.shared.infrastructure.db import create_session_factory  # noqa: E402

SIM = ROOT / "sim_shop"
TEMPLATE = "faq_sales_quote"
TEMPLATE_VERSION: int | None = None  # None = bản mới nhất; đặt bằng --template-version
ALL_SECTIONS = {
    "hours",
    "shipping",
    "payment",
    "returns",
    "warranty",
    "wholesale",
    "promo",
    "invoice",
}


# --------------------------------------------------------------------------- DB và dữ liệu
def eval_url(base: str) -> str:
    url = make_url(base)
    return url.set(database=f"{url.database}_eval").render_as_string(hide_password=False)


async def recreate(url: str) -> None:
    parsed = make_url(url)
    admin = create_async_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{parsed.database}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{parsed.database}"'))
    await admin.dispose()
    cfg = Config(str(ROOT.parents[0] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, cfg, "head")


@dataclasses.dataclass
class Shop:
    key: str
    tenant_id: UUID
    connection_id: UUID
    name: str
    profile: str
    source: str
    sku_by_id: dict[str, str]


def profile_of(knowledge: dict) -> str:
    sections = set(knowledge["sections"])
    if not sections and not knowledge["faq_count"]:
        return "không tài liệu"
    if sections == ALL_SECTIONS and knowledge["policy_file"]:
        return "đủ tài liệu"
    if not knowledge["policy_file"]:
        return "chỉ FAQ"
    return "viết dở"


async def load_shop(factory, key: str) -> Shop:
    folder = SIM / "shops" / key
    meta = json.loads((folder / "shop.json").read_text(encoding="utf-8"))
    async with factory() as s, s.begin():
        seeded = await seed(
            s,
            tenant_name=meta["name"],
            email=f"eval-{key}-{uuid4().hex[:6]}@example.test",
            origins=["http://localhost"],
        )
        tenant = seeded.tenant_id
        conn = (
            await s.execute(
                text("SELECT id FROM channel_connections WHERE public_key = :k"),
                {"k": seeded.public_key},
            )
        ).scalar_one()
        tpl = get_template(TEMPLATE, TEMPLATE_VERSION)
        agent_id = await insert_agent(
            s,
            tenant_id=tenant,
            connection_id=conn,
            name="Bot eval",
            template_key=tpl.key,
            template_version=tpl.version,
            config=json.loads(tpl.model_dump_json()),
            created_by=uuid4(),
        )
        await s.execute(text("UPDATE agents SET mode = 'shadow' WHERE id = :a"), {"a": agent_id})
        # Trần ngày rộng: eval không được dừng giữa chừng vì trần của shop demo.
        await upsert_ai_settings(s, tenant, ai_enabled=True, daily_cost_cap_micro=100_000_000)
        raw = (folder / "catalog.csv").read_text(encoding="utf-8")
        await import_catalog(s, tenant, parse_catalog_csv(raw))
        faq_file, policy_file = folder / "faq.json", folder / "policy.md"
        if faq_file.exists():
            for f in json.loads(faq_file.read_text(encoding="utf-8")):
                await create_entry(s, "faq", tenant, f["question"], f["answer"], "approved")
        if policy_file.exists():
            body = policy_file.read_text(encoding="utf-8")
            await create_entry(s, "doc", tenant, f"Chính sách {meta['name']}", body, "approved")
        await publish(
            s,
            tenant,
            PolicyConfig(
                hours=BusinessHours.model_validate(meta["hours"]),
                paused=SituationConfig(
                    enabled=True, text="Dạ {shop_name} tạm nghỉ tới {next_open} ạ."
                ),
                after_hours=SituationConfig(enabled=True, text="Dạ shop mở lại lúc {next_open} ạ."),
            ),
            uuid4(),
        )
        rows = await s.execute(
            text("SELECT id, sku FROM catalog_items WHERE tenant_id = :t"), {"t": tenant}
        )
        sku_by_id = {str(r[0]): r[1] for r in rows}
    k = meta["knowledge"]
    return Shop(key, tenant, conn, meta["name"], profile_of(k), k["catalog_source"], sku_by_id)


async def add_shop_line(s, tenant: UUID, conv: UUID, conn: UUID, content: str) -> None:
    """Lượt "shop" trong lịch sử: ghi như tin tự động (system) để không đổi `handling`."""
    last = (
        await s.execute(
            text("SELECT last_sequence FROM conversations WHERE id = :c FOR UPDATE"), {"c": conv}
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO messages (id, tenant_id, channel_connection_id, conversation_id, "
            "external_message_id, direction, sender_type, content, status, sequence, occurred_at) "
            "VALUES (:id, :t, :cn, :c, :ext, 'outbound', 'system', :x, 'sent', :q, now())"
        ),
        {
            "id": uuid4(),
            "t": tenant,
            "cn": conn,
            "c": conv,
            "ext": f"eval:{uuid4()}",
            "x": content,
            "q": last + 1,
        },
    )
    await s.execute(
        text("UPDATE conversations SET last_sequence = :q, last_message_at = now() WHERE id = :c"),
        {"q": last + 1, "c": conv},
    )


async def customer_says(factory, shop: Shop, visitor: str, content: str):
    event = NormalizedChannelEvent(
        tenant_id=shop.tenant_id,
        channel_connection_id=shop.connection_id,
        provider="webchat",
        external_message_id=str(uuid4()),
        external_customer_id=visitor,
        direction="inbound",
        message_type="text",
        text=content,
        occurred_at=datetime.now(UTC),
        trace_id=None,
    )
    return await ReceiveInboundMessage(lambda: SqlConversationUnitOfWork(factory))(event)


# --------------------------------------------------------------------------- một nhiệm vụ
async def run_task(factory, shop: Shop, task: dict, handler) -> Outcome:
    visitor = f"eval-{task['id']}"
    conv = None
    for h in task["history"]:
        if h["who"] == "khach":
            conv = (await customer_says(factory, shop, visitor, h["text"])).conversation_id
        else:
            async with factory() as s, s.begin():
                await add_shop_line(s, shop.tenant_id, conv, shop.connection_id, h["text"])
    first = None
    res = None
    for m in task["messages"]:
        res = await customer_says(factory, shop, visitor, m["text"])
        first = first or res.sequence
    at = datetime.fromisoformat(task["messages"][0]["at"])
    req = RunRequest(
        conversation_id=res.conversation_id,
        tenant_id=shop.tenant_id,
        from_sequence=first,
        to_sequence=res.sequence,
        first_seen_at=at,
        due_at=at,
        lease_token=uuid4(),
        attempts=1,
    )
    started = time.monotonic()
    try:
        await PolicyStage(factory, handler, clock=lambda: at).handle(req)
    except Exception:  # provider lỗi ⇒ runner đã đóng run `failed` rồi ném lại để retry
        pass
    latency = int((time.monotonic() - started) * 1000)
    return await read_outcome(factory, shop, req, latency)


async def read_outcome(factory, shop: Shop, req: RunRequest, latency: int) -> Outcome:
    p = {"t": shop.tenant_id, "c": req.conversation_id}
    async with factory() as s:
        run = (
            await s.execute(
                text(
                    "SELECT id, status, skip_reason, intents, cost_micro FROM agent_runs "
                    "WHERE tenant_id = :t AND conversation_id = :c ORDER BY started_at DESC LIMIT 1"
                ),
                p,
            )
        ).first()
        tools, draft, served = [], None, []
        if run:
            tools = list(
                (
                    await s.execute(
                        text(
                            "SELECT name FROM agent_run_steps WHERE run_id = :r AND kind = 'tool'"
                        ),
                        {"r": run[0]},
                    )
                ).scalars()
            )
            draft = (
                await s.execute(
                    text(
                        "SELECT text, sources, grounded, needs_human, reason FROM agent_drafts "
                        "WHERE run_id = :r"
                    ),
                    {"r": run[0]},
                )
            ).first()
            served = [
                f"{a}:{b}"
                for a, b in await s.execute(
                    text("SELECT provider, model FROM usage_events WHERE run_id = :r ORDER BY id"),
                    {"r": run[0]},
                )
            ]
        handling = (
            await s.execute(text("SELECT handling FROM conversations WHERE id = :c"), p)
        ).scalar_one()
        ticket = (
            await s.execute(
                text("SELECT count(*) FROM handoff_requests WHERE conversation_id = :c"), p
            )
        ).scalar_one()
        situation = (
            await s.execute(
                text(
                    "SELECT situation FROM policy_decisions WHERE conversation_id = :c "
                    "AND to_sequence = :q"
                ),
                p | {"q": req.to_sequence},
            )
        ).scalar()
    sources = [
        shop.sku_by_id.get(x["id"], x["id"])
        for x in (draft[1] if draft else [])
        if x.get("type") == "product"
    ]
    return Outcome(
        run_status=run[1] if run else None,
        skip_reason=run[2] if run else None,
        intents=list(run[3] or []) if run else [],
        tools=tools,
        draft_text=draft[0] if draft else None,
        draft_sources=sources,
        grounded=draft[2] if draft else None,
        draft_needs_human=draft[3] if draft else None,
        draft_reason=draft[4] if draft else None,
        handling=handling,
        ticket=ticket > 0,
        situation=situation,
        latency_ms=latency,
        cost_micro=run[4] if run else 0,
        served_by=served,
    )


# --------------------------------------------------------------------------- LLM giả cho CI
class GenericFake:
    """Không gọi mạng: phân loại 'other', soạn nháp 'nhờ nhân viên'. Chỉ để chạy thử harness."""

    TARGET = Target("anthropic", "claude-sonnet-5", max_tokens=1024)

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req: LLMRequest) -> LLMResult:
        self.calls += 1
        if req.output_schema is ClassifyOut:
            parsed = ClassifyOut(
                intents=["other"], confidence_note="", frustration="none", human_decision=False
            )
        else:
            parsed = DraftOut(
                reply="Dạ em nhờ nhân viên hỗ trợ anh/chị ạ.",
                sources=[],
                needs_human=True,
                reason="no_source",
            )
        return LLMResult(
            stop_reason="end_turn",
            text=parsed.model_dump_json(),
            parsed=parsed,
            tool_calls=(),
            usage=Usage(input=200, output=20),
            served_by=self.TARGET,
        )


# --------------------------------------------------------------------------- chọn mẫu, báo cáo
def sample(tasks: list[dict], per_category: int | None) -> list[dict]:
    """Lấy `per_category` ca mỗi nhóm, xoay vòng giữa các shop; xác định (theo id)."""
    ordered = sorted(tasks, key=lambda t: t["id"])
    if not per_category:
        return ordered
    by_cat: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for t in ordered:
        by_cat[t["category"]][t["shop"]].append(t)
    picked = []
    for i, (_, shops) in enumerate(sorted(by_cat.items())):
        queues = [list(v) for _, v in sorted(shops.items())]
        queues = (
            queues[i % len(queues) :] + queues[: i % len(queues)]
        )  # mỗi nhóm bắt đầu ở shop khác
        taken = 0
        while taken < per_category and any(queues):
            for q in queues:
                if q and taken < per_category:
                    picked.append(q.pop(0))
                    taken += 1
    return picked


def pct(ok: int, n: int) -> str:
    return "—" if n == 0 else f"{ok / n:.0%} ({ok}/{n})"


def table(title: str, groups: dict[str, list], dims: list[str]) -> list[str]:
    head = "| Nhóm | Ca | " + " | ".join(dims) + " |"
    lines = [f"### {title}", "", head, "|" + "---|" * (len(dims) + 2)]
    for name, rows in sorted(groups.items()):
        scores = [r[2] for r in rows]
        cells = [pct(*rate(scores, d)) for d in dims]
        lines.append(f"| {name} | {len(rows)} | " + " | ".join(cells) + " |")
    return [*lines, ""]


def report(rows, shops: dict[str, Shop], args, elapsed: float, mode: str) -> str:
    total = summary(rows)
    served = Counter(m for _, o, _ in rows for m in o.served_by)
    statuses = Counter(o.run_status or "no_run" for _, o, _ in rows)
    tpl_version = get_template(TEMPLATE, TEMPLATE_VERSION).version
    models = ", ".join(f"{k} × {v}" for k, v in served.most_common())
    md = [
        "# Báo cáo eval agent (bước 2.12)",
        "",
        f"- Ngày chạy: {datetime.now().astimezone():%Y-%m-%d %H:%M}; chế độ: **{mode}**; "
        f"`PROMPT_VERSION` {PROMPT_VERSION}; template `{TEMPLATE}` v{tpl_version}.",
        f"- Dữ liệu: `backend/evals/sim_shop/` (12 shop giả lập), {len(rows)} nhiệm vụ "
        + (f"({args.per_category} ca mỗi nhóm)." if args.per_category else "(toàn bộ)."),
        f"- Model phục vụ (số lần gọi): {models or 'không gọi LLM'}.",
        f"- Trạng thái run: {', '.join(f'{k} {v}' for k, v in statuses.most_common())}.",
        f"- Chi phí: {total['cost_micro'] / 1e6:.4f} USD; độ trễ mỗi nhiệm vụ (chính sách + agent) "
        f"p50 {total['latency_ms']['p50']} ms, p95 {total['latency_ms']['p95']} ms; tổng thời gian "
        f"{elapsed:.0f} s; {total['llm_free']} run không gọi LLM (rule).",
        "",
        "## Tổng",
        "",
        "| Chiều | Kết quả |",
        "|---|---|",
    ]
    md += [f"| {d} | {pct(*total['dimensions'][d])} |" for d in DIMENSIONS]
    md += [
        "",
        "Chiều không áp dụng cho một ca thì không tính vào mẫu số (vd `amounts` chỉ tính "
        "ca có số tiền kỳ vọng và không cần người).",
        "",
    ]
    dims = ["needs_human", "amounts", "sources", "forbidden", "no_source_safe", "situation"]
    md += table("Theo nhóm nhiệm vụ", _group(rows, lambda t: t["category"]), dims)
    md += table(
        "Theo hồ sơ tài liệu của shop", _group(rows, lambda t: shops[t["shop"]].profile), dims
    )
    md += table("Theo nguồn catalog", _group(rows, lambda t: shops[t["shop"]].source), dims)
    fails = [
        (t, o, s)
        for t, o, s in rows
        if not s.needs_human
        or s.amounts is False
        or s.forbidden is False
        or s.no_source_safe is False
        or not s.situation
    ]
    md += ["## Ca sai (tối đa 15)", "", "| Ca | Kỳ vọng | Thực tế |", "|---|---|---|"]
    for t, o, _ in fails[:15]:
        want = f"chuyển người={t['expected']['needs_human']} {t['expected']['reason']}".strip()
        got = (
            f"{o.run_status}/{o.skip_reason or ''} nháp: "
            f"{(o.draft_text or '—')[:90].replace('|', '/').replace(chr(10), ' ')}"
        )
        md.append(f"| `{t['id']}` | {want} | {got} |")
    return "\n".join(md) + "\n"


def _group(rows, key) -> dict[str, list]:
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[key(r[0])].append(r)
    return groups


# --------------------------------------------------------------------------- main
async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="LLM giả, không gọi mạng")
    ap.add_argument("--per-category", type=int, default=None)
    ap.add_argument("--max-cost-usd", type=float, default=2.0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", help="ghi báo cáo markdown")
    ap.add_argument("--raw", help="ghi kết quả từng ca (jsonl)")
    ap.add_argument("--template-version", type=int, default=None, help="so sánh các bản template")
    args = ap.parse_args()
    global TEMPLATE_VERSION
    TEMPLATE_VERSION = args.template_version

    settings = get_settings()
    url = eval_url(settings.database_url)
    await recreate(url)
    engine = create_async_engine(url)
    factory = create_session_factory(engine)
    tasks = sample(json.loads((SIM / "tasks.json").read_text(encoding="utf-8")), args.per_category)
    shops = {}
    for key in sorted({t["shop"] for t in tasks}):
        shops[key] = await load_shop(factory, key)
    if args.fake:
        llm, mode = GenericFake(), "LLM giả (smoke)"
    else:
        tpl = get_template(TEMPLATE, TEMPLATE_VERSION)
        llm, _ = build_llm(
            settings, {f"{tpl.key}.v{tpl.version}": {tpl.routes.classify, tpl.routes.draft}}
        )
        mode = "LLM thật"
    handler = AgentRunner(factory, llm).handle
    cap = int(args.max_cost_usd * 1e6)
    spent = 0
    lock = asyncio.Semaphore(args.concurrency)
    rows: list = []

    async def one(task: dict) -> None:
        nonlocal spent
        async with lock:
            if spent >= cap:
                return
            o = await run_task(factory, shops[task["shop"]], task, handler)
            spent += o.cost_micro
            rows.append((task, o, score(task["expected"], o)))

    started = time.monotonic()
    await asyncio.gather(*(one(t) for t in tasks))
    elapsed = time.monotonic() - started
    await engine.dispose()
    rows.sort(key=lambda r: r[0]["id"])
    skipped = len(tasks) - len(rows)
    text_ = report(rows, shops, args, elapsed, mode)
    if skipped:
        text_ += f"\n> Dừng sớm vì chạm trần chi phí: {skipped} nhiệm vụ chưa chạy.\n"
    if args.out:
        Path(args.out).write_text(text_, encoding="utf-8")
    if args.raw:
        with open(args.raw, "w", encoding="utf-8") as f:
            for t, o, sc in rows:
                f.write(json.dumps({"id": t["id"]} | as_json(o, sc), ensure_ascii=False) + "\n")
    sys.stdout.reconfigure(encoding="utf-8")
    print(text_)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
