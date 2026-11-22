"""Đo tín hiệu leo thang (bước 2.10) trên bộ demo có nhãn: precision, recall, latency.

    uv run python scripts/eval_escalation.py                 # chỉ tín hiệu hành vi, không tốn tiền
    uv run python scripts/eval_escalation.py --live --out ../docs/evals/escalation-signals.md

`--live` gọi bước phân loại THẬT (route `classify`, giống runner) cho từng ca để lấy tín hiệu ngữ
nghĩa, đo độ trễ và chi phí. Key đọc từ backend/.env, không bao giờ in ra.
"""

import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from app.config.settings import get_settings
from app.modules.agent_management.infrastructure.templates import get_template
from app.modules.agent_runtime.context import Line, RunContext
from app.modules.agent_runtime.llm.factory import build_llm
from app.modules.agent_runtime.llm.port import LLMRequest, Turn
from app.modules.agent_runtime.llm.pricing import cost_micro
from app.modules.agent_runtime.prompts import CLASSIFY, PROMPT_VERSION, SAFETY
from app.modules.agent_runtime.schemas import ClassifyOut
from app.modules.agent_runtime.signals import THRESHOLDS, behavior_signals, score, semantic_signals
from app.modules.conversation_policy.domain.escalation import confusion

CASES = Path(__file__).parents[1] / "evals" / "escalation_demo.json"


def behavior(case: dict) -> list[str]:
    hist = case["history"]
    return behavior_signals(
        [m["text"] for m in hist if m["who"] == "khach"],
        case["new"],
        sum(1 for m in hist if m["who"] != "khach"),
    )


def micro_latency(cases: list[dict], rounds: int = 2000) -> tuple[float, float]:
    """p50, p95 của MỘT lần tính tín hiệu hành vi (micro giây)."""
    samples = []
    for case in cases:
        start = time.perf_counter()
        for _ in range(rounds):
            behavior(case)
        samples.append((time.perf_counter() - start) / rounds * 1e6)
    samples.sort()
    return statistics.median(samples), samples[int(len(samples) * 0.95) - 1]


async def classify_live(cases: list[dict]) -> list[dict]:
    tpl = get_template("faq_sales_quote")
    llm, _ = build_llm(get_settings(), {"eval": {tpl.routes.classify}})
    system = (
        SAFETY,
        CLASSIFY.format(intents="\n".join(f"- {k}: {v}" for k, v in tpl.intents.items())),
    )
    out = []
    for case in cases:
        lines = [
            Line(i, "khach" if m["who"] == "khach" else "nhan_vien", m["text"])
            for i, m in enumerate(case["history"], 1)
        ]
        new = [Line(len(lines) + i, "khach", t) for i, t in enumerate(case["new"], 1)]
        ctx = RunContext("", "", frozenset(), "", tuple(lines), tuple(new), 0)
        start = time.perf_counter()
        res = await llm.complete(
            LLMRequest(
                route=tpl.routes.classify,
                system_blocks=system,
                turns=(Turn(role="user", text=ctx.transcript()),),
                output_schema=ClassifyOut,
                deadline=time.monotonic() + 60,
            )
        )
        ms = (time.perf_counter() - start) * 1000
        parsed = res.parsed
        assert isinstance(parsed, ClassifyOut), f"{case['id']}: no parsed output"
        out.append(
            {
                "ms": ms,
                "frustration": parsed.frustration,
                "human_decision": parsed.human_decision,
                "served_by": f"{res.served_by.provider}:{res.served_by.model}",
                "cost": cost_micro(res.served_by.provider, res.served_by.model, res.usage),
                "turns": len(lines) + len(new),
                "failed_over": [f"{a.target.provider}:{a.reason}" for a in res.failover_from],
            }
        )
    return out


def pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.0%}"


def table(title: str, rows: list[tuple[int, bool]]) -> list[str]:
    """Quét ngưỡng 1..6; cột cuối cho biết ngưỡng nào ứng với độ nhạy của shop."""
    names = {t: n for n, t in THRESHOLDS.items()}
    lines = [f"### {title}", "", "| Ngưỡng | Độ nhạy | TP | FP | FN | TN | Precision | Recall |"]
    lines.append("|---|---|---|---|---|---|---|---|")
    for t in range(1, 7):
        c = confusion(rows, t)
        lines.append(
            f"| ≥ {t} | {names.get(t, '')} | {c.tp} | {c.fp} | {c.fn} | {c.tn} | "
            f"{pct(c.precision)} | {pct(c.recall)} |"
        )
    return [*lines, ""]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    labels = [c["needs_human"] for c in cases]
    beh = [behavior(c) for c in cases]
    p50, p95 = micro_latency(cases)

    md = [
        "# Đo tín hiệu leo thang (bước 2.10)",
        "",
        f"- Ngày: {datetime.now().astimezone():%Y-%m-%d}; `PROMPT_VERSION` {PROMPT_VERSION}",
        f"- Dữ liệu: `backend/evals/escalation_demo.json`, {len(cases)} ca TỔNG HỢP "
        f"({sum(labels)} cần người, {len(cases) - sum(labels)} không). Người viết luật cũng viết "
        "ca thử ⇒ số đo lạc quan, chỉ để so các phương án; phải đo lại trên hội thoại thật.",
        f"- Latency tín hiệu hành vi (Python thuần, mỗi lượt): p50 {p50:.0f} µs, p95 {p95:.0f} µs.",
        "",
    ]
    md += table("Chỉ tín hiệu hành vi", [(score(b), y) for b, y in zip(beh, labels, strict=True)])

    if args.live:
        live = await classify_live(cases)
        sem = [semantic_signals(r["frustration"], r["human_decision"], r["turns"]) for r in live]
        # Ngữ nghĩa không chặn số lượt: để thấy mô hình nói gì kể cả ca ngắn.
        sem_all = [semantic_signals(r["frustration"], r["human_decision"], 99) for r in live]
        ms = sorted(r["ms"] for r in live)
        # "circuit_open" = bỏ qua đích chính ngay, không tốn thời gian chờ.
        waited = [r for r in live if any(not f.endswith("circuit_open") for f in r["failed_over"])]
        direct = sorted(r["ms"] for r in live if r not in waited) or [0.0]
        served = sorted({r["served_by"] for r in live})
        reasons = sorted({f for r in live for f in r["failed_over"]})
        n_fo = len(waited)
        md += [
            f"- Bước phân loại thật (model phục vụ: {', '.join(served)}; {n_fo}/{len(live)} lần "
            f"phải chờ đích chính hỏng rồi mới failover; lý do: {', '.join(reasons) or 'không'}): "
            f"tất cả p50 {statistics.median(ms):.0f} ms, p95 {ms[int(len(ms) * 0.95) - 1]:.0f} ms; "
            f"bỏ các lần failover p50 {statistics.median(direct):.0f} ms, "
            f"p95 {direct[int(len(direct) * 0.95) - 1]:.0f} ms. "
            f"tổng chi phí ${sum(r['cost'] for r in live) / 1e6:.4f} cho {len(live)} lần gọi. "
            "Tín hiệu ngữ nghĩa không thêm lần gọi LLM nào: độ trễ thêm vào run ≈ 0 ngoài vài "
            "token ra.",
            "",
        ]
        md += table(
            "Ngữ nghĩa (chỉ tính khi ≥ 4 lượt, như runner)",
            [(score(s), y) for s, y in zip(sem, labels, strict=True)],
        )
        md += table(
            "Ngữ nghĩa, bỏ điều kiện ≥ 4 lượt",
            [(score(s), y) for s, y in zip(sem_all, labels, strict=True)],
        )
        md += table(
            "Hành vi + ngữ nghĩa (đúng như runner ghi)",
            [(score(b + s), y) for b, s, y in zip(beh, sem, labels, strict=True)],
        )
        md += [
            "### Từng ca",
            "",
            "| Ca | Nhãn | Hành vi | frustration | human_decision | ms | Model |",
            "|---|---|---|---|---|---|---|",
        ]
        md += [
            f"| {c['id']} | {'cần người' if c['needs_human'] else '—'} | {', '.join(b) or '—'} "
            f"| {r['frustration']} | {r['human_decision']} | {r['ms']:.0f} "
            f"| {r['served_by']}{' (failover)' if r['failed_over'] else ''} |"
            for c, b, r in zip(cases, beh, live, strict=True)
        ]
    text = "\n".join(md) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    asyncio.run(main())
