"""Nạp và kiểm tra `routes.toml` (ADR-0015). File sai ⇒ lỗi ngay lúc khởi động."""

import re
import tomllib
from collections.abc import Collection
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.agent_runtime.llm.port import Target
from app.modules.agent_runtime.llm.pricing import PRICES, UnknownPrice

ROUTES_FILE = Path(__file__).with_name("routes.toml")
REQUIRED_ROUTES = ("classify", "draft")
_NAME = r"^[a-z][a-z0-9_]{1,30}$"

Routes = dict[str, tuple[Target, ...]]


class NoTargetAvailable(Exception):
    """Một route không còn đích nào (thiếu key / bị tắt hết)."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _TargetIn(_Strict):
    provider: Literal["anthropic", "openai"]
    model: str = Field(min_length=1, max_length=100)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    max_tokens: int = Field(ge=64, le=16_000)
    timeout_s: float = Field(default=30.0, ge=1, le=120)

    @model_validator(mode="after")
    def _effort_only_for_anthropic(self) -> "_TargetIn":
        if self.effort is not None and self.provider != "anthropic":
            raise ValueError("effort is only supported by anthropic targets")  # không ngầm bỏ qua
        return self


class _RouteIn(_Strict):
    targets: list[_TargetIn] = Field(min_length=1)


class _FileIn(_Strict):
    routes: dict[str, _RouteIn]

    @field_validator("routes")
    @classmethod
    def _names_and_required(cls, routes: dict[str, _RouteIn]) -> dict[str, _RouteIn]:
        bad = [n for n in routes if not re.match(_NAME, n)]
        if bad:
            raise ValueError(f"invalid route names: {bad}")
        missing = [n for n in REQUIRED_ROUTES if n not in routes]
        if missing:
            raise ValueError(f"missing required routes: {missing}")
        return routes


def parse_routes(data: dict) -> Routes:
    parsed = _FileIn.model_validate(data)
    return {
        name: tuple(Target(**t.model_dump()) for t in route.targets)
        for name, route in parsed.routes.items()
    }


def load_routes(path: Path = ROUTES_FILE) -> Routes:
    with path.open("rb") as f:
        return parse_routes(tomllib.load(f))


def restrict_to_available(routes: Routes, providers: Collection[str]) -> Routes:
    """Bỏ các đích của provider không dùng được (thiếu key, bị tắt). Giữ nguyên thứ tự."""
    restricted: Routes = {
        name: tuple(t for t in targets if t.provider in providers)
        for name, targets in routes.items()
    }
    empty = [name for name, targets in restricted.items() if not targets]
    if empty:
        raise NoTargetAvailable(f"routes without an available provider: {', '.join(empty)}")
    return restricted


def validate_prices(routes: Routes) -> None:
    """Mọi đích trong route phải có giá, nếu không chi phí không tính được."""
    missing = sorted(
        {
            t.label
            for targets in routes.values()
            for t in targets
            if (t.provider, t.model) not in PRICES
        }
    )
    if missing:
        raise UnknownPrice(f"no price for: {', '.join(missing)}")
