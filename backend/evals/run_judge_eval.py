"""Chạy bộ vàng của giám khảo so sánh shadow (thiết kế 04 §6.3) trên route `judge` thật.

    uv run python backend/evals/run_judge_eval.py [--out report.md]

Mỗi ca có nhãn người: `sendable` (good = gửi được như hoặc sửa nhẹ; bad = phải viết lại / không
được gửi) và các `verdict` chấp nhận được. Chỉ số chính là độ khớp `sendable`, vì đó là thứ bước 5
dùng. Gọi từng ca một (gateway chỉ chịu khoảng 2 request đồng thời). Đổi model hay prompt của
giám khảo thì chạy lại và so với lần trước.
"""

import argparse
import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "src"))

from app.config.settings import get_settings  # noqa: E402
from app.modules.agent_runtime.llm.factory import build_llm  # noqa: E402
from app.modules.agent_runtime.llm.port import LLMRequest, Turn  # noqa: E402
from app.modules.agent_runtime.llm.pricing import cost_micro  # noqa: E402
from app.modules.shadow.domain.judge import (  # noqa: E402
    JUDGE_PROMPT_VERSION,
    SYSTEM,
    JudgeOut,
    finalize,
    is_sendable,
    user_turn,
)

GOLDEN = ROOT / "judge" / "golden.json"


def load() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


async def main(out: str | None) -> None:
    llm, _ = build_llm(get_settings(), {"judge": {"judge"}})
    rows, cost, models = [], 0, Counter()
    for case in load():
        t = time.monotonic()
        result = await llm.complete(
            LLMRequest(
                route="judge",
                system_blocks=(SYSTEM,),
                turns=(
                    Turn(
                        role="user",
                        text=user_turn(case["customer"], case["draft"], case["staff"], None),
                    ),
                ),
                output_schema=JudgeOut,
                deadline=time.monotonic() + 90,
            )
        )
        j = result.parsed if isinstance(result.parsed, JudgeOut) else None
        verdict, sendable = finalize(None, j) if j else (None, None)
        got = "good" if is_sendable(sendable) else "bad"
        cost += cost_micro(result.served_by.provider, result.served_by.model, result.usage)
        models[result.served_by.model] += 1
        rows.append(
            {
                "id": case["id"],
                "kind": case["kind"],
                "sendable_ok": got == case["sendable"],
                "verdict_ok": verdict in case["verdict_any"],
                "verdict": verdict,
                "sendable": sendable,
                "secs": round(time.monotonic() - t, 1),
                "rationale": j.rationale if j else "",
            }
        )
        print(case["id"], "OK" if rows[-1]["sendable_ok"] else "SAI", verdict, sendable, flush=True)

    n = len(rows)
    ok_s = sum(r["sendable_ok"] for r in rows)
    ok_v = sum(r["verdict_ok"] for r in rows)
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_kind[r["kind"]].append(r)
    lines = [
        "# Bộ vàng của giám khảo",
        "",
        f"- Prompt giám khảo: `{JUDGE_PROMPT_VERSION}`; model: "
        + ", ".join(f"{m} × {k}" for m, k in models.items()),
        f"- Khớp gửi được/không: **{ok_s}/{n}**; verdict trong tập chấp nhận: {ok_v}/{n}",
        f"- Chi phí: {cost / 1e6:.4f} USD; thời gian p50: "
        f"{sorted(r['secs'] for r in rows)[n // 2]} s",
        "",
        "| Nhóm | Khớp gửi được | Khớp verdict |",
        "|---|---|---|",
        *(
            f"| {k} | {sum(r['sendable_ok'] for r in v)}/{len(v)} | "
            f"{sum(r['verdict_ok'] for r in v)}/{len(v)} |"
            for k, v in by_kind.items()
        ),
        "",
        "## Ca sai",
        "",
        *(
            f"- `{r['id']}`: {r['verdict']} / {r['sendable']}: {r['rationale']}"
            for r in rows
            if not r["sendable_ok"] or not r["verdict_ok"]
        ),
    ]
    report = "\n".join(lines) + "\n"
    print(report)
    if out:
        Path(out).write_text(report, encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="ghi báo cáo markdown")
    asyncio.run(main(ap.parse_args().out))
