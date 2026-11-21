"""Agent template: file TOML có version trong repo (review qua PR), validate khi nạp.

Template không cấp quyền vượt mức (§26.1): chỉ được dùng tool trong danh sách read-only đã
đăng ký, và bước 2 chỉ cho mode manual/shadow.
"""

import tomllib
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"

# Tool read-only của bước 2. Tool mới (nhất là có side-effect) phải được thêm có chủ đích.
READ_ONLY_TOOLS = frozenset({"search_products", "get_product"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RouteNames(_Strict):
    """Tên route LLM cho từng bước; provider/model nằm ở `routes.toml` (ADR-0015)."""

    classify: str = Field(pattern=r"^[a-z][a-z0-9_]{1,30}$")
    draft: str = Field(pattern=r"^[a-z][a-z0-9_]{1,30}$")


class Budget(_Strict):
    max_model_calls: int = Field(ge=1, le=10)
    max_tool_calls: int = Field(ge=0, le=20)
    wall_clock_s: int = Field(ge=5, le=300)
    max_cost_micro: int = Field(ge=1)


class AgentTemplate(_Strict):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    version: int = Field(ge=1)
    default_mode: Literal["manual", "shadow"]
    intents: dict[str, str] = Field(min_length=1)
    handoff_intents: list[str]
    tools: list[str]
    routes: RouteNames
    budget: Budget
    instructions: str = Field(min_length=1)
    eval_seeds: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "AgentTemplate":
        unknown_intents = set(self.handoff_intents) - set(self.intents)
        if unknown_intents:
            raise ValueError(f"handoff_intents not in intents: {sorted(unknown_intents)}")
        unknown_tools = set(self.tools) - READ_ONLY_TOOLS
        if unknown_tools:
            raise ValueError(f"tools not allowed: {sorted(unknown_tools)}")
        if "{shop_name}" not in self.instructions:
            raise ValueError("instructions must contain {shop_name}")
        return self


@cache
def load_templates() -> dict[tuple[str, int], AgentTemplate]:
    """Nạp mọi `*.toml` trong thư mục templates. File lỗi ⇒ ném lỗi ngay (không bỏ qua im lặng)."""
    templates: dict[tuple[str, int], AgentTemplate] = {}
    for path in sorted(TEMPLATES_DIR.glob("*.toml")):
        with path.open("rb") as f:
            t = AgentTemplate.model_validate(tomllib.load(f))
        if (t.key, t.version) in templates:
            raise ValueError(f"duplicate template {t.key} v{t.version} ({path.name})")
        templates[(t.key, t.version)] = t
    return templates


def get_template(key: str, version: int | None = None) -> AgentTemplate:
    """Template theo key; không nêu version ⇒ bản mới nhất. Không có ⇒ KeyError."""
    templates = load_templates()
    if version is None:
        versions = [v for (k, v) in templates if k == key]
        if not versions:
            raise KeyError(key)
        version = max(versions)
    return templates[(key, version)]
