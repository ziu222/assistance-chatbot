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


POLICY_DOMAIN = RUNTIME.parent / "conversation_policy" / "domain"
IO_IMPORTS = ("sqlalchemy", "fastapi", "httpx", "redis", "asyncpg", "app.shared.infrastructure")


def test_policy_domain_is_pure():
    """`decide` và giờ làm việc là hàm thuần (§12.2): worker và trình mô phỏng gọi chung."""
    files = sorted(POLICY_DOMAIN.rglob("*.py"))
    assert files, "conversation_policy/domain not found"
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
