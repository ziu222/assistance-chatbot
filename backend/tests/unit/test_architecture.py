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
            offenders += [f"{path.name}: {n}" for n in names if n.startswith(FORBIDDEN_IMPORTS)]
    assert offenders == []


def test_runtime_never_writes_to_messages_table():
    offenders = [
        f"{p.name}: {sql}"
        for p in runtime_files()
        for sql in FORBIDDEN_SQL
        if sql in " ".join(p.read_text(encoding="utf-8").lower().split())
    ]
    assert offenders == []
