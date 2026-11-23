"""Validate generated simulated-shop data against Atlas domain parsers."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from app.modules.agent_runtime.validation import extract_amounts
from app.modules.conversation_policy.domain.hours import BusinessHours
from app.modules.conversation_policy.domain.policy import (
    PolicyConfig,
    PolicyState,
    SituationConfig,
    decide,
)
from app.modules.knowledge.domain.catalog_csv import parse_catalog_csv

ROOT = Path(__file__).resolve().parent
ERRORS: list[str] = []


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    shops = {p.name: load(p / "shop.json") for p in (ROOT / "shops").iterdir() if p.is_dir()}
    tasks = load(ROOT / "tasks.json")
    if len(shops) < 4 or len(tasks) < 400:
        ERRORS.append("need four shops and at least 400 tasks")
    catalogs = {}
    orders = {}
    for key, shop in shops.items():
        folder = ROOT / "shops" / key
        raw = (folder / "catalog.csv").read_text(encoding="utf-8")
        try:
            items = parse_catalog_csv(raw)
            catalogs[key] = {i.sku: i for i in items}
            orders[key] = {item["id"] for item in load(folder / "orders.json")}
        except Exception as exc:
            ERRORS.append(f"{key}: catalog: {exc}")
        try:
            BusinessHours.model_validate(shop["hours"])
            PolicyConfig(hours=BusinessHours.model_validate(shop["hours"]))
        except Exception as exc:
            ERRORS.append(f"{key}: policy: {exc}")
        if len(load(folder / "faq.json")) < 15 or len(load(folder / "faq.json")) > 30:
            ERRORS.append(f"{key}: faq count")
        if len((folder / "policy.md").read_text(encoding="utf-8")) > 20000:
            ERRORS.append(f"{key}: policy too long")
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)) or len(
        {json.dumps(t["messages"], ensure_ascii=False) for t in tasks}
    ) != len(tasks):
        ERRORS.append("duplicate task id or messages")
    counts = Counter(t["category"] for t in tasks)
    for category in (
        "faq",
        "product_info",
        "price_quote",
        "out_of_stock",
        "ambiguous_product",
        "discount_request",
        "order_status",
        "return_request",
        "complaint",
        "human_request",
        "policy_exception",
        "multi_turn",
        "frustration",
        "injection",
        "out_of_scope",
        "after_hours",
        "paused",
        "hard_negative",
    ):
        if counts[category] < 25:
            ERRORS.append(f"category {category} below 25")
    for task in tasks:
        if not set(task["expected"]["intents_any"]) <= {
            "greeting",
            "faq",
            "product_info",
            "price_quote",
            "discount_request",
            "order_intent",
            "complaint",
            "human_request",
            "other",
        }:
            ERRORS.append(f"{task['id']}: invalid intent")
        for source in task["expected"]["sources_include"]:
            if source["sku"] not in catalogs.get(task["shop"], {}):
                ERRORS.append(f"{task['id']}: missing source {source['sku']}")
        faq_text = " ".join(
            item["answer"] for item in load(ROOT / "shops" / task["shop"] / "faq.json")
        )
        policy_text = (ROOT / "shops" / task["shop"] / "policy.md").read_text(encoding="utf-8")
        known_amounts = extract_amounts(faq_text + "\n" + policy_text)
        if task["expected"]["amounts"] and task["expected"]["sources_include"]:
            sku = task["expected"]["sources_include"][0]["sku"]
            amount = task["expected"]["amounts"][0]
            if amount != catalogs[task["shop"]][sku].price_vnd or amount not in (
                known_amounts | {catalogs[task["shop"]][sku].price_vnd}
            ):
                ERRORS.append(f"{task['id']}: amount mismatch")
        order_match = re.search(r"DH-[A-Z]{2}-\d{4}", task["messages"][0]["text"])
        if task["category"] == "order_status" and (
            order_match is None or order_match.group() not in orders[task["shop"]]
        ):
            ERRORS.append(f"{task['id']}: missing order reference")
        if task["expected"]["reason"] not in {
            "",
            "no_source",
            "policy",
            "discount",
            "order",
            "complaint",
            "unclear",
        }:
            ERRORS.append(f"{task['id']}: invalid reason")
        if not task["expected"]["needs_human"] and task["expected"]["reason"] != "":
            ERRORS.append(f"{task['id']}: non-handoff task has reason")
        if task["category"] in {"multi_turn", "frustration"} and not task["history"]:
            ERRORS.append(f"{task['id']}: missing history")
        source_skus = [source["sku"] for source in task["expected"]["sources_include"]]
        if not task["notes"] or (
            not any(token in task["notes"] for token in source_skus)
            and not any(
                token in task["notes"] for token in {"policy.md", "faq.json", "orders.json"}
            )
        ):
            ERRORS.append(f"{task['id']}: note is not traceable")
        hours = BusinessHours.model_validate(shops[task["shop"]]["hours"])
        policy = PolicyConfig(
            hours=hours,
            paused=SituationConfig(enabled=True, text="Đang tạm nghỉ {next_open}"),
            after_hours=SituationConfig(enabled=True, text="Đã hết giờ {next_open}"),
        )
        at = datetime.fromisoformat(task["messages"][0]["at"])
        decision = decide(
            policy,
            PolicyState("bot", True, None, {}),
            at,
            shop_name=shops[task["shop"]]["name"],
        )
        expected_situation = task["expected"]["policy"]["situation"]
        if decision.situation != expected_situation:
            ERRORS.append(
                f"{task['id']}: policy situation {expected_situation!r} != {decision.situation!r}"
            )
    normalized = {
        re.sub(
            r"\d+", "<n>", re.sub(r"[A-Z]{2}-\d{3,4}", "<sku>", t["messages"][0]["text"].lower())
        )
        for t in tasks
    }
    no_diacritic = sum(
        not any(ch in t["messages"][0]["text"] for ch in "ăâđêôơưÁĂÂĐÊÔƠƯ") for t in tasks
    )
    if len(normalized) < 100:
        ERRORS.append(f"too few normalized templates: {len(normalized)}")
    if no_diacritic / len(tasks) < 0.4:
        ERRORS.append("no-diacritic share below 40%")
    if sum(t["difficulty"] == "hard" for t in tasks) / len(tasks) < 0.3:
        ERRORS.append("hard share below 30%")
    personas = {p["id"] for p in load(ROOT / "personas.json")}
    if {t["persona"] for t in tasks} != personas or len(personas) < 8:
        ERRORS.append("not all personas are used")
    print("shops:", len(shops), "tasks:", len(tasks), "categories:", dict(sorted(counts.items())))
    print(
        "needs_human:",
        sum(t["expected"]["needs_human"] for t in tasks),
        "hard:",
        sum(t["difficulty"] == "hard" for t in tasks),
    )
    if ERRORS:
        print("\n".join(ERRORS[:20]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
