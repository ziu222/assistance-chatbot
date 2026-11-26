"""Quy tắc bật/tắt Autopilot theo từng ý định (thiết kế 06 §4, §10, ADR-0039): hàm thuần.

- Ý định được phép bật: `greeting`, `faq`, `product_info`, `price_quote` (kèm kiểm `catalog_stale`).
  Ý định chuyển người, `order_intent`, `other` không bao giờ bật được.
- Lần bật đầu tiên của một ý định cần số đo nguồn mù (`blind`); nguồn đã thấy (`shown`) chỉ dùng
  để bật lại sau khi bị hạ cấp (§10).
- Khóa cấu hình lưu khi bật gồm `template`, `template_version`, `prompt_version`, và `models`
  (model chính của route `classify` và `draft`); không gồm `knowledge_hash` vì đổi tri thức đi qua
  cổng eval chứ không hạ cấp (§6.3).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from app.modules.outbound.domain.gate import NEVER_AUTOPILOT_INTENTS

# Ý định được phép bật tự trả lời (06 §4.1: mở `price_quote` ở A6 kèm kiểm `catalog_stale`).
ENABLEABLE_INTENTS = frozenset({"greeting", "faq", "product_info", "price_quote"})
Source = Literal["blind", "shown"]


class _HasModel(Protocol):
    model: str


class _HasRouteNames(Protocol):
    classify: str
    draft: str


class _HasTemplateRoutes(Protocol):
    routes: _HasRouteNames


@dataclass(frozen=True)
class ReadinessSnapshot:
    ready: bool
    reasons: tuple[str, ...]
    config_key: dict | None


@dataclass(frozen=True)
class EnableDecision:
    allowed: bool
    source: Source | None = None
    config_key: dict | None = None
    reasons: tuple[str, ...] = ()


def primary_models(
    routes: Mapping[str, Sequence[_HasModel]],
    template: _HasTemplateRoutes | None,
) -> dict[str, str] | None:
    """Model của đích đầu tiên (đích chính) cho route `classify` và `draft` của template."""
    if template is None:
        return None
    classify_targets = routes.get(template.routes.classify)
    draft_targets = routes.get(template.routes.draft)
    if not classify_targets or not draft_targets:
        return None
    return {
        "classify": classify_targets[0].model,
        "draft": draft_targets[0].model,
    }


def extract_config_key(raw_config: dict | None) -> dict | None:
    """Rút khóa cấu hình (`template`, `template_version`, `prompt_version`) từ `config_version`."""
    if not raw_config:
        return None
    template = raw_config.get("template")
    template_version = raw_config.get("template_version")
    prompt_version = raw_config.get("prompt_version")
    if not template or template_version is None or not prompt_version:
        return None
    return {
        "template": str(template),
        "template_version": int(template_version),
        "prompt_version": str(prompt_version),
    }


def can_enable_intent(
    intent: str,
    *,
    handoff_intents: frozenset[str] = frozenset(),
    current_config_key: dict | None,
    blind: ReadinessSnapshot | None,
    shown: ReadinessSnapshot | None,
    previously_enabled: bool = False,
    preferred_source: Source | None = None,
) -> EnableDecision:
    """Kiểm tra server-side xem ý định có đủ điều kiện bật Autopilot lúc này không."""
    if intent in NEVER_AUTOPILOT_INTENTS or intent in handoff_intents:
        return EnableDecision(
            False, reasons=("ý định chuyển người hoặc đặt hàng không thể tự trả lời",)
        )
    if intent not in ENABLEABLE_INTENTS:
        return EnableDecision(
            False, reasons=("ý định này chưa được mở tự trả lời ở phiên bản hiện tại",)
        )
    if current_config_key is None:
        return EnableDecision(False, reasons=("chưa xác định được phiên bản cấu hình AI hiện tại",))

    # `snap.config_key` lấy từ `shadow_comparisons.config_version` (3 trường gốc, không có
    # `models`); lọc theo model chính của route draft đã thực hiện ở truy vấn tổng hợp.
    base_config_key = {k: v for k, v in current_config_key.items() if k != "models"}
    sources: tuple[Source, ...] = (preferred_source,) if preferred_source else ("blind", "shown")
    reasons: list[str] = []
    for src in sources:
        snap = blind if src == "blind" else shown
        if src == "shown" and not previously_enabled:
            if preferred_source == "shown" or (blind is None or not blind.ready):
                reasons.append("lần bật đầu tiên cần đạt mức sẵn sàng trên nguồn mù (Shadow)")
            continue
        if snap is None:
            reasons.append(f"chưa có số đo trên nguồn {'mù' if src == 'blind' else 'Learning'}")
            continue
        if not snap.ready:
            reasons.extend(snap.reasons or ("chưa đạt mức sẵn sàng",))
            continue
        if snap.config_key != base_config_key:
            reasons.append("số đo thuộc phiên bản cấu hình AI cũ")
            continue
        return EnableDecision(True, source=src, config_key=current_config_key)

    dedup = tuple(dict.fromkeys(reasons)) or ("chưa đạt mức sẵn sàng",)
    return EnableDecision(False, reasons=dedup)
