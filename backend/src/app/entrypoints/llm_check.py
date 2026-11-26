"""Kiểm tra cấu hình LLM: gọi mỗi đích của mỗi route một lần, in provider/model/token/chi phí.
`python -m app.entrypoints.llm_check [--structured]`. Tốn tiền thật (vài token); không in key."""

import argparse
import asyncio
import sys
from collections import Counter

from app.config.settings import get_settings
from app.modules.agent_runtime.llm.factory import build_backends, build_llm
from app.modules.agent_runtime.llm.failover import FailoverLLM
from app.modules.agent_runtime.llm.port import AllTargetsFailed, LLMPort, LLMRequest, Turn
from app.modules.agent_runtime.llm.pricing import cost_micro
from app.modules.authoring.domain.writing import BrandProfile, WritingOut, system_blocks, user_turn

STRUCTURED_RUNS = 5
STRUCTURED_SAMPLE = "Shop mình bán bình giữ nhiệt, giao hàng toàn quốc trong 2-4 ngày."


def _error_label(exc: Exception) -> str:
    if isinstance(exc, AllTargetsFailed) and exc.attempts:
        reason = exc.attempts[-1].reason
        return "InvalidOutput" if reason == "invalid_output" else f"{type(exc).__name__}:{reason}"
    return type(exc).__name__


async def probe_structured(
    llm: LLMPort, req: LLMRequest, *, runs: int = STRUCTURED_RUNS
) -> tuple[int, list[str]]:
    """Gọi tuần tự `runs` lần với schema; trả `(số lần parse được, danh sách tên lỗi)`."""
    ok = 0
    errors: list[str] = []
    for _ in range(runs):
        try:
            res = await llm.complete(req)
        except Exception as exc:  # noqa: BLE001 - ghi tên lỗi từng lần thử
            errors.append(_error_label(exc))
            continue
        if req.output_schema is not None and isinstance(res.parsed, req.output_schema):
            ok += 1
        else:
            errors.append("UnparsedOutput")
    return ok, errors


def _authoring_request() -> LLMRequest:
    return LLMRequest(
        route="authoring",
        system_blocks=system_blocks(BrandProfile(), "polish", "faq"),
        turns=(Turn(role="user", text=user_turn(STRUCTURED_SAMPLE)),),
        output_schema=WritingOut,
    )


async def _run_structured(backends: dict, routes: dict) -> int:
    targets = routes.get("authoring", ())
    req = _authoring_request()
    failed = 0
    for t in targets:
        one = FailoverLLM({"authoring": (t,)}, backends)
        ok, errors = await probe_structured(one, req, runs=STRUCTURED_RUNS)
        err_note = ""
        if errors:
            counts = Counter(errors)
            err_note = " errors=" + ",".join(f"{k}x{v}" for k, v in sorted(counts.items()))
        status = "OK  " if ok == STRUCTURED_RUNS else "FAIL"
        if ok < STRUCTURED_RUNS:
            failed += 1
        print(f"{status} authoring {t.label:34} parsed={ok}/{STRUCTURED_RUNS}{err_note}")
    return 1 if failed else 0


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check LLM route targets.")
    parser.add_argument(
        "--structured",
        action="store_true",
        help="Probe route 'authoring' 5x per target with WritingOut structured schema.",
    )
    args = parser.parse_args(argv)

    s = get_settings()
    backends = build_backends(s)
    print("providers:", ", ".join(sorted(backends)) or "(none)")
    _, routes = build_llm(s, backends=backends)
    if args.structured:
        return await _run_structured(backends, routes)

    failed = 0
    for name, targets in routes.items():
        for t in targets:
            # mỗi lần chỉ một đích: route tạm một-đích để không bị failover che lỗi
            one = FailoverLLM({name: (t,)}, backends)
            req = LLMRequest(
                route=name,
                system_blocks=("Reply with the single word: ok",),
                turns=(Turn(role="user", text="ping"),),
            )
            try:
                r = await one.complete(req)
                u = r.usage
                print(
                    f"OK   {name:9} {t.label:34} in={u.input} out={u.output} "
                    f"cost={cost_micro(t.provider, t.model, u)} micro-USD"
                )
            except Exception as exc:  # noqa: BLE001 - báo lỗi từng đích rồi đi tiếp
                failed += 1
                print(f"FAIL {name:9} {t.label:34} {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
