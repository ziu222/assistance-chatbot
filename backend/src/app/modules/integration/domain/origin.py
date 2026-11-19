"""Allowlist origin nơi được nhúng widget.

Giá trị do tenant cấu hình và được ghép vào header CSP, nên chỉ nhận đúng dạng
scheme://host[:port]; mọi thứ khác bị bỏ để không chèn được directive/header.
"""

import re
from typing import Any

_ORIGIN = re.compile(r"https?://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d{1,5})?")


def valid_origins(allowed: Any) -> list[str]:
    if not isinstance(allowed, list):
        return []
    result = []
    for item in allowed:
        if isinstance(item, str):
            origin = item.strip().lower().removesuffix("/")
            if _ORIGIN.fullmatch(origin):  # fullmatch: "$" của re vẫn khớp trước "\n"
                result.append(origin)
    return result


def frame_ancestors(origins: list[str]) -> str:
    return " ".join(origins) if origins else "'none'"
