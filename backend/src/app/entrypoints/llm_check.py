"""Kiểm tra cấu hình LLM: gọi mỗi đích của mỗi route một lần, in provider/model/token/chi phí.
`python -m app.entrypoints.llm_check`. Tốn tiền thật (vài token); không in key."""

import asyncio
import sys

from app.config.settings import get_settings
from app.modules.agent_runtime.llm.factory import build_backends, build_llm
from app.modules.agent_runtime.llm.failover import FailoverLLM
from app.modules.agent_runtime.llm.port import LLMRequest, Turn
from app.modules.agent_runtime.llm.pricing import cost_micro


async def main() -> int:
    s = get_settings()
    backends = build_backends(s)
    print("providers:", ", ".join(sorted(backends)) or "(none)")
    _, routes = build_llm(s, backends=backends)
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
