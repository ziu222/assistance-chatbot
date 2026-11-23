"""Bất biến §1.4: không có đường code nào đưa output của agent tới khách.

Bản nháp nằm ở `agent_drafts`, không bao giờ ở `messages` (draft ≠ sent message).
"""

import ast
from pathlib import Path

RUNTIME = Path(__file__).parents[2] / "src" / "app" / "modules" / "agent_runtime"
# Các module có thể gửi tin hoặc chạm vào kênh của khách.
FORBIDDEN_IMPORTS = (
    "app.modules.conversation",
    "app.modules.integration",
    "app.modules.inbox",
    "app.modules.outbound",  # cổng gửi tin tự động (2.9): agent không được gọi
)
FORBIDDEN_SQL = ("insert into messages", "update messages", "delete from messages")


def _under(name: str, packages: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in packages)


def runtime_files() -> list[Path]:
    files = sorted(RUNTIME.rglob("*.py"))
    assert files, "agent_runtime not found: the architecture test would pass vacuously"
    return files


def test_runtime_does_not_import_message_sending_modules():
    offenders = []
    for path in runtime_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}: {n}" for n in names if _under(n, FORBIDDEN_IMPORTS)]
    assert offenders == []


def test_runtime_never_writes_to_messages_table():
    offenders = [
        f"{p.name}: {sql}"
        for p in runtime_files()
        for sql in FORBIDDEN_SQL
        if sql in " ".join(p.read_text(encoding="utf-8").lower().split())
    ]
    assert offenders == []


PURE_DOMAINS = [
    RUNTIME.parent / m / "domain" for m in ("conversation_policy", "authoring", "orders")
]
IO_IMPORTS = ("sqlalchemy", "fastapi", "httpx", "redis", "asyncpg", "app.shared.infrastructure")


def test_policy_domain_is_pure():
    """`decide` và giờ làm việc là hàm thuần (§12.2): worker và trình mô phỏng gọi chung."""
    files = sorted(f for d in PURE_DOMAINS for f in d.rglob("*.py"))
    assert len({f.parent for f in files}) >= 2, "pure domain folders not found"
    offenders = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}: {n}" for n in names if n.startswith(IO_IMPORTS)]
    assert offenders == []


MODULES = RUNTIME.parent


def test_only_outbound_writes_messages_with_raw_sql():
    """§12.6: một cổng gửi duy nhất. Use case nhận tin/nhân viên trả lời ghi qua ORM của module
    `conversation`; mọi câu SQL thô ghi `messages` chỉ được nằm trong `outbound`."""
    offenders = [
        f"{p.relative_to(MODULES)}"
        for p in sorted(MODULES.rglob("*.py"))
        if "outbound" not in p.parts
        and "insert into messages" in " ".join(p.read_text(encoding="utf-8").lower().split())
    ]
    assert offenders == []


def test_outbound_sends_only_system_messages():
    src = (MODULES / "outbound" / "canned.py").read_text(encoding="utf-8")
    assert "'outbound', 'system'" in src and "'bot'" not in src
