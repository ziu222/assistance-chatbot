"""Quy tắc bật/tắt Autopilot theo từng ý định (thiết kế 06 §4, §10, ADR-0039): hàm thuần.

- Chỉ một tập nhỏ ý định được phép bật ở A2–A3 (`greeting`, `faq`, `product_info`); `price_quote`
  chờ kiểm độ mới catalog (A6). Ý định chuyển người, `order_intent`, `other` không bao giờ bật được.
- Lần bật đầu tiên của một ý định cần số đo nguồn mù (`blind`); nguồn đã thấy (`shown`) chỉ dùng
  để bật lại sau khi bị hạ cấp (§10).
- Khóa cấu hình lưu khi bật gồm `template`, `template_version`, `prompt_version` (không gồm
  `knowledge_hash` vì đổi tri thức đi qua cổng eval chứ không hạ cấp, §6.3).
"""

from dataclasses import dataclass
from typing import Literal

from app.modules.outbound.domain.gate import NEVER_AUTOPILOT_INTENTS

# Ý định được phép bật tự trả lời ở A2–A5 (`price_quote` mở ở A6 cùng kiểm độ mới catalog).
ENABLEABLE_INTENTS = frozenset({"greeting", "faq", "product_info"})
Source = Literal["blind", "shown"]


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
        if snap.config_key != current_config_key:
            reasons.append("số đo thuộc phiên bản cấu hình AI cũ")
            continue
        return EnableDecision(True, source=src, config_key=current_config_key)

    dedup = tuple(dict.fromkeys(reasons)) or ("chưa đạt mức sẵn sàng",)
    return EnableDecision(False, reasons=dedup)
