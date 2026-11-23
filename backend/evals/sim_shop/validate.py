"""Kiểm bộ shop giả lập bằng chính code của Atlas; thoát mã 1 nếu có lỗi.

    uv run python backend/evals/sim_shop/validate.py

Ngoài cấu trúc, file này kiểm những lỗi từng lọt qua review: câu lặp theo mẫu, hậu tố giả để
né kiểm trùng, hội thoại "nhiều lượt" chỉ có một tin, ca hết hàng trỏ vào hàng còn tồn, sản
phẩm sai ngành, chính sách mỏng, lộ thuật ngữ hệ thống, nhãn chuyển người mâu thuẫn.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import tomllib
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1] / "src"))

from app.modules.agent_runtime.signals import behavior_signals  # noqa: E402
from app.modules.agent_runtime.validation import extract_amounts  # noqa: E402
from app.modules.conversation_policy.domain.hours import BusinessHours  # noqa: E402
from app.modules.conversation_policy.domain.policy import (  # noqa: E402
    PolicyConfig,
    PolicyState,
    SituationConfig,
    decide,
    detect_handoff,
)
from app.modules.knowledge.domain.catalog_csv import parse_catalog_csv  # noqa: E402
from app.modules.knowledge.domain.text import fold  # noqa: E402

CATEGORIES = {
    "faq",
    "missing_info",
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
}
INTENTS = {
    "greeting",
    "faq",
    "product_info",
    "price_quote",
    "discount_request",
    "order_intent",
    "complaint",
    "human_request",
    "other",
}
REASONS = {"", "no_source", "policy", "discount", "order", "complaint", "unclear"}
SYSTEM_WORDS = ("cấu hình", "faq", "tool", "json", "database", "hệ thống")
SEEDS = ROOT.parents[1] / "src/app/modules/agent_management/templates/evals/faq_sales_quote.v1.toml"
MIN = {
    "tasks": 400,
    "per_category": 25,
    "hard_share": 0.30,
    "no_diacritic_share": 0.40,
    "distinct_last_share": 0.65,
    "policy_chars": 1000,  # chỉ áp cho shop khai báo đủ mọi mục chính sách
    "multi_variant_share": 0.2,
    "shops": 10,
    "shops_without_docs": 2,  # shop không có cả file chính sách lẫn FAQ
    "no_source_share": 0.10,  # ca mà đáp án đúng là "chưa có căn cứ, nhờ nhân viên"
}
TOPIC_SECTION = {
    "ship": "shipping",
    "freeship": "shipping",
    "return": "returns",
    "cod": "payment",
    "hours": "hours",
    "warranty": "warranty",
    "wholesale": "wholesale",
    "invoice": "invoice",
    "promo": "promo",
}
ALL_SECTIONS = {
    "hours",
    "shipping",
    "payment",
    "returns",
    "warranty",
    "wholesale",
    "promo",
    "invoice",
}
MARKET_SOURCES = {"shopee_like", "tiktok_like"}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def shape(text: str) -> str:
    """Dạng câu sau khi bỏ dấu, mã và số: hai câu chỉ khác SKU/số đếm là cùng một mẫu."""
    t = fold(text)
    t = re.sub(r"\b[a-z]{2}-(?:[a-z]{2}-)?\d+\b", "<id>", t)
    return re.sub(r"\d[\d.,]*", "<n>", t)


def check(errors: list[str]) -> dict:  # noqa: C901, PLR0912, PLR0915
    shops, cat = {}, {}
    for folder in sorted(p for p in (ROOT / "shops").iterdir() if p.is_dir()):
        key = folder.name
        shop = load(folder / "shop.json")
        raw = (folder / "catalog.csv").read_text(encoding="utf-8")
        items = parse_catalog_csv(raw)  # parser import thật của Atlas
        rows = list(csv.DictReader(io.StringIO(raw)))
        # Shop thật có thể không có file chính sách hoặc FAQ: thiếu là hợp lệ, không phải lỗi.
        pf, ff = folder / "policy.md", folder / "faq.json"
        policy = pf.read_text(encoding="utf-8") if pf.exists() else ""
        faq = load(ff) if ff.exists() else []
        kn = shop["knowledge"]
        orders = load(folder / "orders.json")
        hours = BusinessHours.model_validate(shop["hours"])
        shops[key] = {
            "shop": shop,
            "policy": policy,
            "faq": faq,
            "hours": hours,
            "orders": {o["id"]: o for o in orders},
            "known": extract_amounts(policy + "\n" + " ".join(f["answer"] for f in faq)),
        }
        cat[key] = {i.sku: i for i in items}
        if len(items) != kn["skus"] or kn["policy_file"] != pf.exists():
            errors.append(f"{key}: shop.json knowledge does not match files")
        if kn["faq_count"] != len(faq) or not set(kn["sections"]) <= ALL_SECTIONS:
            errors.append(f"{key}: knowledge.faq_count/sections inconsistent")
        if sum(len(i.variants) > 1 for i in items) < len(items) * MIN["multi_variant_share"]:
            errors.append(f"{key}: too few SKUs with several variants")
        for r in rows:  # tiêu đề kiểu sàn có emoji/từ khóa: chỉ cần chứa đúng tên loại
            if not any(t in r["name"] for t in shop["product_types"]):
                errors.append(
                    f"{key}: {r['sku']} '{r['name']}' is not a {shop['industry']} product"
                )
        prices = [i.price_vnd for i in items]
        steps = Counter(b - a for a, b in zip(prices, prices[1:], strict=False))
        if steps and steps.most_common(1)[0][1] > len(prices) * 0.2:
            errors.append(f"{key}: prices follow a fixed step {steps.most_common(1)[0]}")
        full = set(kn["sections"]) == ALL_SECTIONS and kn["policy_file"]
        if (full and len(policy) < MIN["policy_chars"]) or len(policy) > 20000:
            errors.append(f"{key}: policy.md length {len(policy)}")
        for sec in kn["sections"]:  # mục khai báo có phải thật sự xuất hiện trong tài liệu
            if not policy and not faq:
                errors.append(f"{key}: declares section {sec} but has no documents")
        if kn["catalog_source"] in MARKET_SOURCES:
            raw_items = load(folder / "raw_listing.json")["items"]
            by_sku = {x["seller_sku"]: x for x in raw_items}
            for it in items:
                src = by_sku.get(it.sku)
                if not src or src["price_sale"] != it.price_vnd or src["title"] != it.name:
                    errors.append(f"{key}: {it.sku} catalog.csv differs from raw_listing.json")
            shops[key]["original"] = {
                k: x["price_original"]
                for k, x in by_sku.items()
                if x["price_original"] > x["price_sale"]
            }
        if re.search(r"\d,\d{3}đ", policy + json.dumps(faq, ensure_ascii=False)):
            errors.append(f"{key}: mixed number format (use 35.000đ)")
        if len(faq) > 30:
            errors.append(f"{key}: {len(faq)} FAQ entries")
        for f in faq:
            if any(w in f["answer"].lower() for w in SYSTEM_WORDS):
                errors.append(f"{key}: FAQ leaks system wording: {f['answer'][:60]}")
        for o in orders:
            for it in o["items"]:
                if it["sku"] not in cat[key]:
                    errors.append(f"{key}: order {o['id']} has unknown SKU {it['sku']}")

    if len(shops) < MIN["shops"]:
        errors.append(f"only {len(shops)} shops")
    bare = sum(not s["policy"] and not s["faq"] for s in shops.values())
    if bare < MIN["shops_without_docs"]:
        errors.append(f"only {bare} shops without any documents")
    tasks = load(ROOT / "tasks.json")
    personas = {p["id"] for p in load(ROOT / "personas.json")}
    if len(tasks) < MIN["tasks"]:
        errors.append(f"only {len(tasks)} tasks")
    counts = Counter(t["category"] for t in tasks)
    for c in CATEGORIES:
        if counts[c] < MIN["per_category"]:
            errors.append(f"category {c}: {counts[c]} < {MIN['per_category']}")
    if len({t["id"] for t in tasks}) != len(tasks):
        errors.append("duplicate task ids")

    customer = [m["text"] for t in tasks for m in t["messages"]]
    customer += [h["text"] for t in tasks for h in t["history"] if h["who"] == "khach"]
    if any(re.search(r"\(m[aã] \d+\)", x) for x in customer):
        errors.append("artificial '(mã N)' suffix in customer text")
    no_dia = sum(fold(x) == " ".join(x.lower().split()) for x in customer) / len(customer)
    if no_dia < MIN["no_diacritic_share"]:
        errors.append(f"no-diacritic share {no_dia:.2f} < {MIN['no_diacritic_share']}")
    last = Counter(shape(t["messages"][-1]["text"]) for t in tasks)
    distinct = len(last) / len(tasks)
    if distinct < MIN["distinct_last_share"]:
        errors.append(f"only {len(last)} distinct message shapes for {len(tasks)} tasks")
    hard = sum(t["difficulty"] == "hard" for t in tasks) / len(tasks)
    if hard < MIN["hard_share"]:
        errors.append(f"hard share {hard:.2f} < {MIN['hard_share']}")
    unused = personas - {t["persona"] for t in tasks}
    if unused:
        errors.append(f"personas never used: {sorted(unused)}")

    for t in tasks:
        tid, e, s = t["id"], t["expected"], shops[t["shop"]]
        texts = [m["text"] for m in t["messages"]]
        if t["category"] not in CATEGORIES or t["persona"] not in personas:
            errors.append(f"{tid}: unknown category/persona")
        if not set(e["intents_any"]) <= INTENTS or e["reason"] not in REASONS:
            errors.append(f"{tid}: invalid intent or reason")
        if not e["needs_human"] and e["reason"]:
            errors.append(f"{tid}: reason '{e['reason']}' without handoff")
        if not e["needs_human"] and detect_handoff(PolicyConfig(), texts) is not None:
            errors.append(f"{tid}: labelled no-handoff but Atlas phrase rule would hand off")
        # Đáp án theo tri thức shop CÓ: chủ đề shop chưa viết ⇒ chuyển người vì thiếu căn cứ.
        sections = set(s["shop"]["knowledge"]["sections"])
        if "topic" in e:
            known = TOPIC_SECTION[e["topic"]] in sections
            if known and e["reason"] == "no_source":
                errors.append(f"{tid}: shop documents {e['topic']} but label says no_source")
            if not known and (not e["needs_human"] or e["reason"] != "no_source"):
                errors.append(f"{tid}: shop has no {e['topic']} section but label is not no_source")
        if e["reason"] == "no_source" and e["amounts"]:
            errors.append(f"{tid}: no_source case must not expect amounts")
        skus = [x["sku"] for x in e["sources_include"] if x["type"] == "product"]
        if t["category"] == "price_quote":
            for sku in skus:
                orig = s.get("original", {}).get(sku)
                if orig and f"{orig:,}".replace(",", ".") + "đ" not in e["must_not_say"]:
                    errors.append(f"{tid}: listing shows original price {orig} but not forbidden")
        for sku in skus + e.get("candidates", []):
            if sku not in cat[t["shop"]]:
                errors.append(f"{tid}: unknown SKU {sku}")
        allowed = s["known"] | {cat[t["shop"]][k].price_vnd for k in skus if k in cat[t["shop"]]}
        for a in e["amounts"]:
            if a not in allowed:
                errors.append(f"{tid}: amount {a} not in catalog/FAQ/policy of {t['shop']}")
        if any(m in str(e["amounts"]) for m in e["must_not_say"]):
            errors.append(f"{tid}: must_not_say conflicts with amounts")
        for sku in skus:
            if sku not in t["notes"]:
                errors.append(f"{tid}: notes do not cite {sku}")
        all_text = " ".join(texts + [h["text"] for h in t["history"]])
        for oid in re.findall(r"DH-[A-Z]{2}-\d{4}", all_text):
            if oid not in s["orders"]:
                errors.append(f"{tid}: unknown order {oid}")
            elif oid not in t["notes"] and t["category"] == "order_status":
                errors.append(f"{tid}: notes do not cite {oid}")
        if t["category"] == "out_of_stock":
            v = e.get("variant")
            item = cat[t["shop"]].get(skus[0]) if skus else None
            hit = (
                item
                and v
                and any(
                    x["size"] == (v["size"] or None)
                    and x["color"] == (v["color"] or None)
                    and x["stock"] == 0
                    for x in item.variants
                )
            )
            if not hit:
                errors.append(f"{tid}: out_of_stock does not point at a variant with stock 0")
        if t["category"] == "price_quote" and not e["amounts"]:
            errors.append(f"{tid}: price question without expected amount")
        if t["category"] == "injection" and not e["must_not_say"]:
            errors.append(f"{tid}: injection without must_not_say")
        if t["category"] in ("multi_turn", "frustration") and len(t["history"]) + len(texts) < 4:
            errors.append(f"{tid}: fewer than 4 turns")
        if t["category"] == "frustration":
            earlier = [h["text"] for h in t["history"] if h["who"] == "khach"]
            shop_turns = sum(h["who"] != "khach" for h in t["history"])
            if not behavior_signals(earlier, texts, shop_turns):
                errors.append(f"{tid}: frustration case triggers no behaviour signal")
        # Nhãn tình huống phải khớp `decide` thật với tạm nghỉ + ngoài giờ được bật.
        config = PolicyConfig(
            hours=s["hours"],
            paused=SituationConfig(enabled=True, text="Tạm nghỉ đến {next_open}"),
            after_hours=SituationConfig(enabled=True, text="Hết giờ, mở lại {next_open}"),
        )
        got = decide(
            config,
            PolicyState("bot", not t["history"], None, {}),
            datetime.fromisoformat(t["messages"][0]["at"]),
            shop_name=s["shop"]["name"],
        )
        if got.situation != e["policy"]["situation"]:
            errors.append(
                f"{tid}: policy {e['policy']['situation']!r} but decide says {got.situation!r}"
            )

    no_source = sum(t["expected"]["reason"] == "no_source" for t in tasks) / len(tasks)
    if no_source < MIN["no_source_share"]:
        errors.append(f"no_source share {no_source:.2f} < {MIN['no_source_share']}")
    seeds = tomllib.loads(SEEDS.read_text(encoding="utf-8"))["cases"]
    ids = {t["id"] for t in tasks}
    if len(seeds) != 40 or any(c["id"] not in ids or "messages" not in c for c in seeds):
        errors.append("eval seeds must be 40 cases from tasks.json with messages")
    if {c["category"] for c in seeds} != CATEGORIES:
        errors.append("eval seeds do not cover every category")
    return {
        "tasks": len(tasks),
        "shops": len(shops),
        "no_source": round(no_source, 2),
        "no_diacritic": no_dia,
        "distinct_last": distinct,
        "hard": hard,
        "needs_human": sum(t["expected"]["needs_human"] for t in tasks),
        "difficulty": dict(Counter(t["difficulty"] for t in tasks)),
        "personas": dict(Counter(t["persona"] for t in tasks)),
    }


def main() -> int:
    errors: list[str] = []
    stats = check(errors)
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    for e in errors[:50]:
        print("ERROR", e)
    if len(errors) > 50:
        print(f"... and {len(errors) - 50} more")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
