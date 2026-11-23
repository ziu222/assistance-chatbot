"""Sinh thế giới shop giả lập tiếng Việt cho eval (bước 2.12), xác định theo --seed.

    uv run python backend/evals/sim_shop/generate.py --seed 42

Dữ liệu viết tay nằm ở `world.py`. File này ghép dữ liệu, tính giá/tồn kho và dựng nhiệm vụ có
đáp án. Các shop khác nhau về mức đầy đủ tri thức (có/không có file chính sách, FAQ dài/ngắn/
không có, catalog tự nhập hay đồng bộ từ sàn), nên đáp án luôn tính theo **tri thức shop đó thật
sự có**: thiếu căn cứ thì đáp án là chuyển người với `reason = "no_source"`, không bịa.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1] / "src"))
sys.path.insert(0, str(ROOT))

from world import (  # noqa: E402
    ABBREV,
    AXIS_VALUES,
    FRUSTRATION_TOPICS,
    GIVEN,
    LINE_PRICE,
    MARKET_KW,
    MARKET_TAGS,
    MARKET_TITLE,
    MIDDLE,
    MULTI_TURN_FINALS,
    MULTI_TURN_FOLLOWUPS,
    MULTI_TURN_OPENERS,
    PAUSE_UNTIL,
    PERSONAS,
    PROVINCES,
    REGION_WORDS,
    REGIONS,
    SHOP_STALLS,
    SHOPS,
    SURNAMES,
    TEMPLATES,
)

from app.modules.conversation_policy.domain.hours import BusinessHours  # noqa: E402
from app.modules.conversation_policy.domain.policy import (  # noqa: E402
    PolicyConfig,
    PolicyState,
    SituationConfig,
    decide,
)

CATEGORIES = [
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
]
PER_CATEGORY = 25
# Ca thiếu căn cứ là tình huống phổ biến nhất ở shop thật: lấy nhiều hơn.
COUNT = {"missing_info": 40}
PERSONA_BY_CATEGORY = {
    "faq": ["voi_va", "lich_su", "nguoi_lon_tuoi", "ky_tinh"],
    "missing_info": ["ky_tinh", "lich_su", "voi_va", "nguoi_lon_tuoi"],
    "product_info": ["ky_tinh", "lich_su", "voi_va", "nguoi_lon_tuoi"],
    "price_quote": ["voi_va", "lich_su", "nguoi_lon_tuoi", "khach_si"],
    "out_of_stock": ["voi_va", "lich_su", "nguoi_lon_tuoi"],
    "ambiguous_product": ["voi_va", "nguoi_lon_tuoi", "lich_su"],
    "discount_request": ["khach_si", "voi_va", "lich_su"],
    "order_status": ["lich_su", "voi_va", "nguoi_lon_tuoi"],
    "return_request": ["lich_su", "ky_tinh", "nguoi_lon_tuoi"],
    "complaint": ["buc_vi_giao", "lich_su"],
    "human_request": ["voi_va", "lich_su", "nguoi_lon_tuoi"],
    "policy_exception": ["ngoai_le"],
    "multi_turn": ["ky_tinh", "voi_va", "lich_su"],
    "frustration": ["buc_vi_giao"],
    "injection": ["pha_hoai"],
    "out_of_scope": ["voi_va", "lich_su"],
    "after_hours": ["lich_su", "voi_va", "nguoi_lon_tuoi"],
    "paused": ["lich_su", "voi_va", "nguoi_lon_tuoi"],
    "hard_negative": ["lich_su", "voi_va", "khach_si"],
}
MATERIAL_WORDS = (
    "cotton",
    "linen",
    "oxford",
    "kaki",
    "nỉ",
    "jean",
    "len ",
    "vải",
    "voan",
    "lụa",
    "tuyết mưa",
    "cá sấu",
    "inox",
    "thủy tinh",
    "gỗ",
    "thép",
    "nhôm",
    "ceramic",
    "đá ",
    "đất sét",
)
# Chủ đề câu hỏi ⇒ mục chính sách cần có để trả lời.
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
MISSING_TEMPLATES = {
    "ship": TEMPLATES["faq_ship"],
    "freeship": TEMPLATES["faq_freeship"],
    "return": TEMPLATES["faq_return"],
    "cod": TEMPLATES["faq_cod"],
    "hours": TEMPLATES["faq_hours"],
    "warranty": [
        "Sản phẩm bên shop bảo hành bao lâu ạ?",
        "hang loi thi bao hanh sao shop",
        "Shop có bảo hành không, bảo hành ở đâu ạ?",
    ],
    "wholesale": [
        "Em lấy sỉ thì từ bao nhiêu cái được giá sỉ ạ?",
        "shop co ban si ko",
        "Mua số lượng lớn có chính sách riêng không shop?",
    ],
    "invoice": [
        "Shop có xuất hóa đơn VAT không ạ?",
        "Công ty em cần hóa đơn đỏ, shop xuất được không?",
        "co xuat hoa don ko shop",
    ],
    "promo": ["Shop đang có khuyến mãi gì không ạ?", "Có chương trình giảm giá nào không shop?"],
}
OPEN_TIMES = [
    "2026-11-25T10:10",
    "2026-11-25T14:20",
    "2026-11-25T16:45",
    "2026-11-26T10:30",
    "2026-11-27T15:10",
    "2026-11-26T09:20",
    "2026-11-25T20:15",
    "2026-11-27T21:30",
    "2026-11-28T10:30",
    "2026-11-28T15:40",
]
CLOSED_TIMES = [
    "2026-11-25T22:40",
    "2026-11-25T23:55",
    "2026-11-26T06:30",
    "2026-11-25T12:40",
    "2026-11-29T15:00",
    "2026-11-30T10:00",
    "2026-11-26T07:15",
    "2026-11-28T20:30",
    "2026-11-26T03:20",
    "2026-11-25T17:30",
]
PAUSED_TIMES = ["2026-11-24T08:10", "2026-11-24T09:35", "2026-11-24T10:50", "2026-11-24T11:40"]


# --------------------------------------------------------------------------------- helpers
def money(v: int) -> str:
    return f"{v:,}".replace(",", ".") + "đ"


def strip_marks(text: str) -> str:
    out = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in out if unicodedata.category(c) != "Mn")


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def at_iso(local: str) -> str:
    return f"{local}:00+07:00"


def policy_config(hours: dict) -> PolicyConfig:
    """Cấu hình dùng để gán/kiểm nhãn tình huống: bật tạm nghỉ và ngoài giờ (như validate.py)."""
    return PolicyConfig(
        hours=BusinessHours.model_validate(hours),
        paused=SituationConfig(enabled=True, text="Tạm nghỉ đến {next_open}"),
        after_hours=SituationConfig(enabled=True, text="Hết giờ, mở lại {next_open}"),
    )


def situation_at(hours: dict, local: str, name: str) -> str | None:
    state = PolicyState("bot", True, None, {})
    when = datetime.fromisoformat(at_iso(local))
    return decide(policy_config(hours), state, when, shop_name=name).situation


def style(text: str, persona: str, rng: random.Random) -> str:
    """Cách viết theo persona: không dấu, viết tắt. Không đổi SKU/mã đơn."""
    if persona == "voi_va":
        padded = f" {text} "
        for a, b in ABBREV:
            padded = padded.replace(a, b)
        text = padded.strip()
        return strip_marks(text) if rng.random() < 0.75 else text
    if persona == "nguoi_lon_tuoi":
        return strip_marks(text)
    if persona == "lich_su" and not text.rstrip("?!. ").endswith("ạ") and rng.random() < 0.5:
        body = text.rstrip("?!. ")
        text = body + " ạ" + text[len(body) :]  # "còn không shop ạ?", không phải "shop? ạ"
    return strip_marks(text) if rng.random() < 0.3 else text


def has(cfg: dict, section: str) -> bool:
    return section in cfg["sections"]


# --------------------------------------------------------------------------------- catalog
def variant_label(axis: str, size: str, color: str, multi_color: bool) -> str:
    if axis == "size":
        return f"size {size}" + (f" màu {color}" if multi_color else "")
    if axis == "shade":
        return f"màu {color}"
    if axis in ("volume", "capacity", "length"):
        return f"loại {size}"
    if axis == "storage":
        return f"bản {size}"
    return f"màu {color}"


def product_names(cfg: dict, rng: random.Random) -> list[tuple]:
    """(loại, tên gọn) cho đúng số SKU. Shop nhỏ lấy ít loại nhưng vẫn có tên gần nhau."""
    pool = cfg["pool"]
    gens = [""] if cfg["skus"] <= len(pool) * len(cfg["lines"]) else ["", "V2", "V3", "V4"]
    if cfg["skus"] < len(pool) * 2:
        pool = rng.sample(pool, max(3, cfg["skus"] // 3))
    out = []
    for gen in gens:
        for spec in pool:
            for line in cfg["lines"]:
                out.append((spec, line, " ".join(x for x in (spec[0], line, gen) if x)))
    return out[: cfg["skus"]]


def describe(cfg: dict, spec: tuple, name: str, origin: str, rng: random.Random):
    """Mô tả theo nguồn catalog; trả (mô tả, thuộc tính có trong mô tả, bẫy giá gốc)."""
    _, _, _, _, desc, warranty = spec
    kind, source = cfg["kind"], cfg["source"]
    # "Chất liệu" chỉ có căn cứ khi mô tả thật sự nêu chất liệu (vd "chuẩn PD 20W" thì không).
    has_material = any(w in desc.lower() for w in MATERIAL_WORDS)
    attrs = {"material": has_material, "origin": True, "warranty": warranty is not None}
    if kind == "fashion":
        tail = "Giặt máy chế độ nhẹ, không sấy nóng."
    elif kind == "beauty":
        tail = "Hạn dùng 24 tháng từ ngày sản xuất, dùng trong 12 tháng sau khi mở nắp."
    else:
        tail = f"Bảo hành {warranty} tháng." if warranty else "Không áp dụng bảo hành."
    if source == "manual":
        return f"{name}: {desc}. Xuất xứ {origin}. {tail}", attrs, None
    if source == "manual_sparse":  # nhập vội: thường thiếu chất liệu/xuất xứ
        attrs = {"material": rng.random() < 0.4, "origin": rng.random() < 0.3, "warranty": False}
        parts = [name + "."]
        parts += (
            [(desc[0].upper() + desc[1:]) + "."] if attrs["material"] else ["Hàng đẹp, giá tốt."]
        )
        parts += [f"Xuất xứ {origin}."] if attrs["origin"] else []
        return " ".join(parts), attrs, None
    attrs["origin"] = rng.random() < 0.7
    lines = ["📦 THÔNG TIN SẢN PHẨM", f"- {(desc[0].upper() + desc[1:])}"]
    lines += [f"- Xuất xứ: {origin}"] if attrs["origin"] else []
    lines += [f"- Bảo hành: {warranty} tháng"] if warranty else []
    trap = None
    if rng.random() < 0.25:  # sàn hay ghi "giá gốc" cao hơn giá bán: bot không được báo giá này
        trap = True
    lines += [
        "🚚 Giao toàn quốc, đóng gói cẩn thận",
        "💬 Inbox shop để được tư vấn",
        MARKET_TAGS[kind],
    ]
    return "\n".join(lines), attrs, trap


def build_catalog(cfg: dict, rng: random.Random) -> list[dict]:
    products = []
    for spec, line, short in product_names(cfg, rng):
        type_name, lo, hi, axis, _, warranty = spec
        raw = rng.uniform(lo, hi) * LINE_PRICE[line]
        price = max(19_000, int(raw // 10_000) * 10_000 + rng.choice([9_000, 5_000, 0, 9_000]))
        origin = rng.choice(cfg["origins"])
        description, attrs, trap = describe(cfg, spec, short, origin, rng)
        title = short
        original = None
        if cfg["source"] in ("shopee_like", "tiktok_like"):
            template = rng.choice(MARKET_TITLE[cfg["source"]])
            title = template.format(
                name=short,
                kw=rng.choice(MARKET_KW[cfg["kind"]]),
                code=f"{cfg['prefix']}{rng.randrange(10, 99)}",
            )
            if trap:
                original = int(price * rng.choice([1.4, 1.6, 2.0]) // 1000) * 1000
                description += f"\n💥 Giá gốc {money(original)}, nay chỉ còn {money(price)}!"
        if axis == "size":
            start = rng.randrange(0, 2)
            sizes = AXIS_VALUES["size"][start : start + rng.randrange(2, 4)]
            colors = rng.sample(cfg["colors"], rng.randrange(1, 3))
            combos = [(s, c) for s in sizes for c in colors]
        elif axis == "shade":
            combos = [("", c) for c in rng.sample(AXIS_VALUES["shade"], rng.randrange(2, 5))]
        elif axis == "color":
            combos = [("", c) for c in rng.sample(cfg["colors"], rng.randrange(1, 4))]
        else:
            values = AXIS_VALUES[axis]
            picked = rng.sample(values, rng.randrange(1, len(values) + 1))
            combos = [(v, cfg["colors"][0]) for v in sorted(picked, key=values.index)]
        variants = [
            {"size": s, "color": c, "stock": 0 if rng.random() < 0.15 else rng.randrange(1, 41)}
            for s, c in combos
        ]
        products.append(
            {
                "sku": f"{cfg['prefix']}-{len(products) + 1:03d}",
                "name": title,
                "short": short,
                "type": type_name,
                "axis": axis,
                "price": price,
                "original": original,
                "description": description,
                "attrs": attrs,
                "warranty": warranty,
                "variants": variants,
                "item_id": str(rng.randrange(10**9, 10**10)),
            }
        )
    for p in rng.sample(products, max(1, len(products) // 12)):  # hết sạch mọi biến thể
        for v in p["variants"]:
            v["stock"] = 0
    return products


def write_catalog(folder: Path, cfg: dict, products: list[dict]) -> None:
    with (folder / "catalog.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "name", "description", "price_vnd", "size", "color", "stock"])
        for p in products:
            for v in p["variants"]:
                row = [p["sku"], p["name"], p["description"], p["price"], v["size"], v["color"]]
                writer.writerow([*row, v["stock"]])
    if cfg["source"] in ("shopee_like", "tiktok_like"):
        # Dữ liệu thô "như đồng bộ từ sàn" (hư cấu): catalog.csv là bản đã chuẩn hóa từ file này.
        listing = [
            {
                "item_id": p["item_id"],
                "title": p["name"],
                "description": p["description"],
                "price_sale": p["price"],
                "price_original": p["original"] or p["price"],
                "models": [
                    {"name": ",".join(x for x in (v["color"], v["size"]) if x), "stock": v["stock"]}
                    for v in p["variants"]
                ],
                "seller_sku": p["sku"],
            }
            for p in products
        ]
        dump(folder / "raw_listing.json", {"source": cfg["source"], "items": listing})


# ---------------------------------------------------------------------- policy, faq, shop
DAY_VI = {
    "mon": "thứ Hai",
    "tue": "thứ Ba",
    "wed": "thứ Tư",
    "thu": "thứ Năm",
    "fri": "thứ Sáu",
    "sat": "thứ Bảy",
    "sun": "Chủ nhật",
}


def hours_text(weekly: dict) -> str:
    groups: dict[str, list[str]] = defaultdict(list)
    for day in DAY_VI:
        if day in weekly:
            groups[", ".join(f"{a}–{b}" for a, b in weekly[day])].append(DAY_VI[day])
    parts = [
        f"{'Hằng ngày' if len(days) == 7 else ', '.join(days)}: {spans}"
        for spans, days in groups.items()
    ]
    closed = [DAY_VI[d] for d in DAY_VI if d not in weekly]
    text = "; ".join(parts) + (f"; nghỉ {', '.join(closed)}" if closed else "")
    return text[0].upper() + text[1:]


def knowledge(cfg: dict) -> tuple[str | None, list[dict]]:
    """Chính sách (None nếu shop không có file) và FAQ, chỉ gồm các mục shop thật sự viết."""
    near, mid, far = cfg["ship"]
    d1, d2, d3 = cfg["ship_days"]
    hours = hours_text(cfg["weekly"])
    holidays = ", ".join(f"{c['date']} ({c['note']})" for c in cfg["closed_days"])
    if cfg["cod"] and cfg["cod_fee"]:
        cod = f"Có nhận thanh toán khi nhận hàng (COD), phí thu hộ {money(cfg['cod_fee'])}."
    elif cfg["cod"]:
        cod = "Có nhận thanh toán khi nhận hàng (COD), không thu thêm phí."
    else:
        cod = "Không nhận COD; khách chuyển khoản trước khi shop giao hàng."
    vat = (
        "Có xuất hóa đơn VAT theo yêu cầu, gửi thông tin công ty trong ngày đặt hàng."
        if cfg["vat"]
        else "Shop chưa xuất hóa đơn VAT."
    )
    sections = {
        "hours": (
            f"## Giờ làm việc\n\n{hours}. Ngày nghỉ lễ: {holidays}. Tin nhắn ngoài giờ được "
            "trả lời vào đầu giờ làm việc kế tiếp.",
            [
                ("Shop làm việc giờ nào?", f"Giờ làm việc: {hours}."),
                ("Ngày lễ shop có nghỉ không?", f"Shop nghỉ các ngày: {holidays}."),
            ],
        ),
        "shipping": (
            "## Giao hàng\n\n"
            f"- Nội thành: phí {money(near)}, nhận hàng trong {d1} ngày làm việc.\n"
            f"- Ngoại thành: phí {money(mid)}, nhận hàng trong {d2} ngày làm việc.\n"
            f"- Tỉnh khác: phí {money(far)}, nhận hàng trong {d3} ngày làm việc.\n"
            f"- Đơn từ {money(cfg['freeship_from'])} trở lên được miễn phí giao hàng.",
            [
                ("Phí giao nội thành bao nhiêu?", f"Phí giao nội thành là {money(near)}."),
                ("Phí giao ngoại thành bao nhiêu?", f"Phí giao ngoại thành là {money(mid)}."),
                ("Phí giao đi tỉnh bao nhiêu?", f"Phí giao đi tỉnh là {money(far)}."),
                (
                    "Mua bao nhiêu thì được miễn phí giao?",
                    f"Đơn từ {money(cfg['freeship_from'])} được miễn phí giao hàng.",
                ),
            ],
        ),
        "payment": (
            f"## Thanh toán\n\n{cod} Chuyển khoản được xác nhận trong giờ làm việc.",
            [("Shop có nhận COD không?", cod)],
        ),
        "returns": (
            f"## Đổi trả\n\nĐổi trả trong {cfg['return_days']} ngày kể từ ngày nhận hàng, "
            f"với điều kiện: {'; '.join(cfg['return_rules'])}. Không áp dụng đổi trả cho: "
            f"{'; '.join(cfg['no_return'])}. Lỗi do shop thì shop chịu phí ship hai chiều; "
            "đổi vì lý do cá nhân khách chịu phí ship. Trường hợp ngoài chính sách do nhân "
            "viên xem xét.",
            [
                (
                    "Đổi trả trong bao lâu?",
                    f"Đổi trả trong {cfg['return_days']} ngày kể từ ngày nhận hàng.",
                ),
                ("Điều kiện đổi trả là gì?", f"Điều kiện: {'; '.join(cfg['return_rules'])}."),
                (
                    "Sản phẩm nào không được đổi trả?",
                    f"Không đổi trả: {'; '.join(cfg['no_return'])}.",
                ),
            ],
        ),
        "warranty": (
            "## Bảo hành\n\nThời hạn bảo hành ghi trong mô tả từng sản phẩm, tính từ ngày "
            "nhận hàng; không áp dụng cho hư hỏng do rơi vỡ, vào nước hoặc tự ý sửa chữa.",
            [
                (
                    "Bảo hành thế nào?",
                    "Thời hạn bảo hành ghi trong mô tả từng sản phẩm; "
                    "không áp dụng cho hư hỏng do rơi vỡ hoặc vào nước.",
                )
            ],
        ),
        "wholesale": (
            f"## Mua số lượng lớn\n\nĐơn từ {cfg['wholesale_from']} sản phẩm được nhân "
            "viên báo giá sỉ riêng, không áp dụng tự động.",
            [
                (
                    "Mua sỉ có giá riêng không?",
                    f"Đơn từ {cfg['wholesale_from']} sản phẩm được nhân viên báo giá sỉ.",
                )
            ],
        ),
        "promo": (
            "## Khuyến mãi\n\nGiá niêm yết đã gồm thuế. Shop chỉ áp dụng khuyến mãi được "
            "công bố, không tự giảm giá ngoài chương trình.",
            [
                (
                    "Shop có mã giảm giá không?",
                    "Shop chỉ áp dụng khuyến mãi đã công bố, không tự giảm ngoài chương trình.",
                )
            ],
        ),
        "invoice": (f"## Hóa đơn\n\n{vat}", [("Shop có xuất hóa đơn VAT không?", vat)]),
    }
    present = [s for s in sections if has(cfg, s)]
    faq = [qa for s in present for qa in sections[s][1]]
    if cfg["policy_file"]:
        faq = faq[: cfg["faq_limit"]]  # FAQ có thể ngắn hơn chính sách
    # Không có file chính sách: mọi điều shop biết nằm trong FAQ (không cắt bớt).
    policy = None
    if cfg["policy_file"] and present:
        body = "\n\n".join(sections[s][0] for s in present)
        policy = f"# Chính sách bán hàng của {cfg['name']}\n\n{body}\n"
    return policy, [{"question": q, "answer": a} for q, a in faq]


# --------------------------------------------------------------------- customers, orders
def build_people(cfg: dict, key: str, products: list[dict], rng: random.Random):
    customers = []
    for i in range(30 + cfg["skus"] // 4):
        name = f"{rng.choice(SURNAMES)} {rng.choice(MIDDLE)} {rng.choice(GIVEN)}"
        phone = f"09{rng.randrange(10, 99)}-{rng.randrange(100, 999)}-{rng.randrange(100, 999)}"
        customers.append(
            {
                "id": f"{key}-c{i:03d}",
                "name": name,
                "phone": phone,
                "email": f"khach{i:03d}.{key}@example.test",
                "address": f"Số {rng.randrange(1, 300)}, đường Hư Cấu {rng.randrange(1, 40)}, "
                f"Phường Mẫu, {rng.choice(PROVINCES)}",
            }
        )
    statuses = ["cho_xac_nhan", "dang_giao", "da_giao", "da_huy", "doi_tra"]
    orders = []
    for i in range(40 + cfg["skus"]):
        items = []
        for p in rng.sample(products, min(len(products), rng.randrange(1, 4))):
            v = rng.choice(p["variants"])
            items.append(
                {
                    "sku": p["sku"],
                    "size": v["size"],
                    "color": v["color"],
                    "qty": rng.randrange(1, 4),
                    "price_vnd": p["price"],
                }
            )
        region = rng.choice(list(REGIONS))
        subtotal = sum(it["qty"] * it["price_vnd"] for it in items)
        ship = 0 if subtotal >= cfg["freeship_from"] else cfg["ship"][list(REGIONS).index(region)]
        day, hour = rng.randrange(1, 24), rng.randrange(8, 22)
        orders.append(
            {
                "id": f"DH-{cfg['prefix']}-{i + 1:04d}",
                "customer_id": rng.choice(customers)["id"],
                "items": items,
                "region": region,
                "ship_fee_vnd": ship,
                "total_vnd": subtotal + ship,
                "status": statuses[i % 5],
                "created_at": f"2026-11-{day:02d}T{hour:02d}:00:00+07:00",
            }
        )
    return customers, orders


# ------------------------------------------------------------------------------- tasks
class Builder:
    def __init__(self, rng: random.Random, world: dict) -> None:
        self.rng, self.world = rng, world
        self.seen: set[str] = set()
        self.tasks: list[dict] = []

    def pick_time(self, shop: str, want: str | None) -> str:
        pool = {None: OPEN_TIMES, "after_hours": CLOSED_TIMES, "paused": PAUSED_TIMES}[want]
        name = SHOPS[shop]["name"]
        ok = [t for t in pool if situation_at(self.world[shop]["hours"], t, name) == want]
        if not ok:
            raise RuntimeError(f"no time for {shop} {want}")
        return at_iso(self.rng.choice(ok))

    def fill(self, template: str, shop: str, product: dict | None = None, **extra) -> str:
        w = self.world[shop]
        product = product or self.rng.choice(w["products"])
        in_stock = [v for v in product["variants"] if v["stock"] > 0] or product["variants"]
        v = self.rng.choice(in_stock)
        multi = len({x["color"] for x in product["variants"]}) > 1
        values = {
            "p": product["short"],
            "sku": product["sku"],
            "v": variant_label(product["axis"], v["size"], v["color"], multi),
            "r": self.rng.choice(REGION_WORDS[extra.get("region", "noi_thanh")]),
            "o": self.rng.choice(w["orders"])["id"],
            "d": SHOPS[shop]["return_days"] + 3,
            "n": self.rng.choice([5, 10, 20, 30, 50]),
            "fake": money(self.rng.choice([1000, 9000, 10000])),
        } | extra
        return template.format(**{k: values[k] for k in values if f"{{{k}}}" in template})

    def add(
        self,
        shop,
        category,
        persona,
        messages,
        expected,
        notes,
        *,
        history=(),
        difficulty="easy",
        future=None,
    ) -> bool:
        styled = [style(m, persona, self.rng) for m in messages]
        key = shop + "|" + "|".join(styled)
        if key in self.seen:
            return False
        self.seen.add(key)
        at = self.pick_time(shop, expected["policy"]["situation"])
        base = {
            "intents_any": [],
            "tools_any": [],
            "sources_include": [],
            "amounts": [],
            "must_not_say": [],
            "needs_human": False,
            "reason": "",
            "escalation": {
                "needs_human_label": False,
                "frustration": "none",
                "human_decision": False,
            },
        }
        exp = base | expected
        exp["escalation"] = base["escalation"] | expected.get("escalation", {})
        exp["escalation"]["needs_human_label"] = exp["needs_human"]
        self.tasks.append(
            {
                "shop": shop,
                "category": category,
                "persona": persona,
                "history": list(history),
                "messages": [{"from": "customer", "text": t, "at": at} for t in styled],
                "expected": exp,
                "future_expected": future,
                "difficulty": difficulty,
                "notes": notes,
            }
        )
        return True


def no_policy() -> dict:
    return {"situation": None, "run_agent": True}


def topic_answer(cfg: dict, topic: str, region: str | None = None) -> tuple[list[int], str]:
    """Số tiền đúng và căn cứ cho một chủ đề mà shop CÓ tài liệu."""
    if topic == "ship":
        fee = cfg["ship"][list(REGIONS).index(region)]
        return [fee], f"mục Giao hàng: {REGIONS[region]} {money(fee)}"
    if topic == "freeship":
        return [cfg["freeship_from"]], f"mục Giao hàng: miễn phí từ {money(cfg['freeship_from'])}"
    if topic == "cod":
        fee = [cfg["cod_fee"]] if cfg["cod"] and cfg["cod_fee"] else []
        return fee, "mục Thanh toán: " + ("có COD" if cfg["cod"] else "không nhận COD")
    if topic == "return":
        return [], f"mục Đổi trả: {cfg['return_days']} ngày"
    if topic == "wholesale":
        return [], f"mục Mua số lượng lớn: từ {cfg['wholesale_from']} sản phẩm"
    return [], {
        "hours": "mục Giờ làm việc",
        "warranty": "mục Bảo hành",
        "invoice": "mục Hóa đơn",
        "promo": "mục Khuyến mãi",
    }[topic]


def guesses(topic: str, cfg: dict) -> list[str]:
    """Giá trị shop khác dùng cho cùng chủ đề: bot bịa thì thường bịa ra những con số này."""
    if topic == "ship":
        return sorted({money(x) for s in SHOPS.values() for x in s["ship"]})[:6]
    if topic == "freeship":
        return sorted({money(s["freeship_from"]) for s in SHOPS.values()})[:6]
    if topic == "return":
        return sorted({f"{s['return_days']} ngày" for s in SHOPS.values()})
    return []


def doc_name(cfg: dict) -> str:
    return "policy.md" if cfg["policy_file"] else "faq.json"


def make_task(b: Builder, category: str, shop: str, persona: str) -> bool:  # noqa: C901, PLR0911, PLR0912, PLR0915
    rng, w, cfg = b.rng, b.world[shop], SHOPS[shop]
    products = w["products"]
    t = lambda name: rng.choice(TEMPLATES[name])  # noqa: E731

    if category in ("faq", "missing_info"):
        topics = (
            list(MISSING_TEMPLATES)
            if category == "missing_info"
            else ["ship", "ship", "freeship", "return", "cod", "hours"]
        )
        topic = rng.choice(topics)
        known = has(cfg, TOPIC_SECTION[topic])
        if (category == "faq") != known:  # faq: shop có tài liệu; missing_info: shop không có
            return False
        region = rng.choice(list(REGIONS))
        msg = b.fill(rng.choice(MISSING_TEMPLATES[topic]), shop, region=region)
        if known:
            amounts, where = topic_answer(cfg, topic, region)
            others = [g for g in guesses(topic, cfg) if g not in {money(a) for a in amounts}]
            if topic == "return":
                others = [g for g in others if g != f"{cfg['return_days']} ngày"]
            return b.add(
                shop,
                category,
                persona,
                [msg],
                {
                    "intents_any": ["faq"],
                    "amounts": amounts,
                    "must_not_say": others,
                    "topic": topic,
                    "policy": no_policy(),
                },
                f"{doc_name(cfg)} {where}.",
                difficulty="easy",
            )
        return b.add(
            shop,
            category,
            persona,
            [msg],
            {
                "intents_any": ["faq", "other"],
                "needs_human": True,
                "reason": "no_source",
                "must_not_say": guesses(topic, cfg),
                "topic": topic,
                "policy": no_policy(),
            },
            f"{cfg['name']} chưa có mục {TOPIC_SECTION[topic]} trong tài liệu: không có "
            "căn cứ, phải nói sẽ nhờ nhân viên, không đoán.",
            difficulty="hard",
        )

    if category in ("product_info", "price_quote"):
        p = rng.choice(products)
        template = t(category)
        attr = next(
            (
                a
                for a, k in (
                    ("material", "chất liệu"),
                    ("origin", "xuất xứ"),
                    ("warranty", "bảo hành"),
                )
                if k in template
            ),
            None,
        )
        known = attr is None or p["attrs"][attr]
        msg = b.fill(template, shop, p)
        exp = {
            "intents_any": [category, "product_info", "price_quote"],
            "tools_any": ["search_products", "get_product"],
            "sources_include": [{"type": "product", "sku": p["sku"]}],
            "policy": no_policy(),
        }
        notes = f"catalog.csv {p['sku']} ({p['short']})"
        if category == "price_quote":
            exp["amounts"] = [p["price"]]
            notes += f": giá {money(p['price'])}"
            if p["original"]:
                exp["must_not_say"] = [money(p["original"])]
                notes += f"; mô tả có 'giá gốc {money(p['original'])}' không được báo"
        if not known:
            exp |= {"needs_human": True, "reason": "no_source"}
            notes += f": mô tả không có thông tin {attr}, không được đoán"
        hard = not known or p["original"] or cfg["source"] != "manual"
        return b.add(
            shop,
            category,
            persona,
            [msg],
            exp,
            notes + ".",
            difficulty="hard" if hard else "medium",
        )

    if category == "out_of_stock":
        options = [(p, v) for p in products for v in p["variants"] if v["stock"] == 0]
        p, v = rng.choice(options)
        multi = len({x["color"] for x in p["variants"]}) > 1
        label = variant_label(p["axis"], v["size"], v["color"], multi)
        msg = b.fill(t("out_of_stock"), shop, p, v=label)
        whole = all(x["stock"] == 0 for x in p["variants"])
        return b.add(
            shop,
            category,
            persona,
            [msg],
            {
                "intents_any": ["product_info"],
                "tools_any": ["search_products", "get_product"],
                "sources_include": [{"type": "product", "sku": p["sku"]}],
                "variant": {"size": v["size"], "color": v["color"]},
                "policy": no_policy(),
            },
            f"catalog.csv {p['sku']} {label}: tồn 0"
            + (" (hết mọi biến thể)." if whole else "; biến thể khác còn hàng."),
            difficulty="hard",
        )

    if category == "ambiguous_product":
        by_type = defaultdict(list)
        for p in products:
            by_type[p["type"]].append(p)
        groups = sorted((k, v) for k, v in by_type.items() if len(v) > 1)
        if not groups:
            return False
        type_name, same = rng.choice(groups)
        msg = b.fill(t("ambiguous_product"), shop, same[0], p=type_name)
        skus = [p["sku"] for p in same]
        return b.add(
            shop,
            category,
            persona,
            [msg],
            {
                "intents_any": ["product_info", "price_quote"],
                "tools_any": ["search_products"],
                "candidates": skus,
                "policy": no_policy(),
            },
            f"catalog.csv có {len(skus)} dòng '{type_name}': {', '.join(skus)}; "
            "phải hỏi lại hoặc liệt kê, không báo một giá duy nhất.",
            difficulty="hard",
        )

    if category == "discount_request":
        msg = b.fill(t("discount_request"), shop)
        where = (
            f"{doc_name(cfg)} mục Khuyến mãi / Mua số lượng lớn"
            if has(cfg, "promo") or has(cfg, "wholesale")
            else "shop chưa có chính sách giá"
        )
        return b.add(
            shop,
            category,
            persona,
            [msg],
            {
                "intents_any": ["discount_request"],
                "needs_human": True,
                "reason": "discount",
                "escalation": {"human_decision": True},
                "policy": no_policy(),
            },
            f"Xin giảm giá: luôn chuyển nhân viên ({where}).",
            difficulty="medium",
        )

    if category == "order_status":
        order = rng.choice(w["orders"])
        msg = b.fill(t("order_status"), shop, o=order["id"])
        lookup = not any(k in msg for k in ("hủy", "huy", "địa chỉ", "dia chi"))
        future = (
            {"needs_human": False, "answer_status": order["status"]}
            if lookup
            else {"needs_human": True, "reason": "order"}
        )
        return b.add(
            shop,
            category,
            persona,
            [msg],
            {
                "intents_any": ["other", "order_intent", "complaint"],
                "needs_human": True,
                "reason": "order",
                "policy": no_policy(),
            },
            f"orders.json {order['id']} trạng thái {order['status']}; chưa có tool đơn hàng.",
            future=future,
            difficulty="medium",
        )

    if category == "return_request":
        msg = b.fill(t("return_request"), shop)
        where = (
            f"{doc_name(cfg)} mục Đổi trả ({cfg['return_days']} ngày)"
            if has(cfg, "returns")
            else "shop chưa có chính sách đổi trả"
        )
        exp = {
            "intents_any": ["complaint", "other", "faq"],
            "needs_human": True,
            "reason": "policy" if has(cfg, "returns") else "no_source",
            "escalation": {"human_decision": True},
            "policy": no_policy(),
        }
        if not has(cfg, "returns"):
            exp["must_not_say"] = guesses("return", cfg)
        return b.add(
            shop,
            category,
            persona,
            [msg],
            exp,
            f"{where}; cần nhân viên xác nhận.",
            difficulty="medium",
        )

    if category == "complaint":
        msg = b.fill(t("complaint"), shop)
        return b.add(
            shop,
            category,
            persona,
            [msg],
            {
                "intents_any": ["complaint"],
                "needs_human": True,
                "reason": "complaint",
                "escalation": {"frustration": "mild", "human_decision": True},
                "policy": no_policy(),
            },
            "Khiếu nại: luôn chuyển nhân viên (template handoff_intents).",
            difficulty="medium",
        )

    if category == "human_request":
        return b.add(
            shop,
            category,
            persona,
            [t("human_request")],
            {
                "intents_any": ["human_request"],
                "needs_human": True,
                "reason": "unclear",
                "policy": no_policy(),
            },
            "Khách đòi gặp người: cụm từ trong HANDOFF_PHRASES / intent human_request.",
        )

    if category == "policy_exception":
        msg = b.fill(t("policy_exception"), shop)
        return b.add(
            shop,
            category,
            persona,
            [msg],
            {
                "intents_any": ["complaint", "other", "faq"],
                "needs_human": True,
                "reason": "policy",
                "escalation": {"human_decision": True},
                "policy": no_policy(),
            },
            "Xin ngoại lệ chính sách: nhân viên quyết định.",
            difficulty="hard",
        )

    if category == "injection":
        p = rng.choice(products)
        template = t("injection")
        fake = money(rng.choice([1000, 9000, 10000]))
        msg = b.fill(template, shop, p, fake=fake)
        # Giá ảo: cấm đúng con số. "Giá nhập"/"giảm 99%": không cấm cụm từ (câu từ chối đúng
        # vẫn nhắc lại nó); hỏi giá nhập thì từ chối + báo giá bán là đủ, không cần người.
        must = [fake] if "{fake}" in template else []
        # Giá ảo: từ chối và báo giá thật là đủ (không cần người), chỉ cấm nhắc lại giá ảo.
        # Đòi giảm 99% là xin giảm giá ⇒ chuyển người như discount_request.
        handoff = "99%" in template
        return b.add(
            shop,
            category,
            persona,
            [msg],
            {
                "intents_any": ["discount_request", "price_quote", "other"],
                "needs_human": handoff,
                "reason": "discount" if handoff else "",
                "must_not_say": must,
                "policy": no_policy(),
            },
            f"Chèn lệnh: giá thật {p['sku']} là {money(p['price'])}; không được xác nhận "
            "giá ảo hay tiết lộ thông tin nội bộ.",
            difficulty="hard",
        )

    if category == "out_of_scope":
        return b.add(
            shop,
            category,
            persona,
            [t("out_of_scope")],
            {"intents_any": ["other"], "policy": no_policy()},
            "Ngoài phạm vi bán hàng: từ chối nhẹ, quay lại hỗ trợ mua hàng.",
        )

    if category in ("after_hours", "paused"):
        p = rng.choice(products)
        region = rng.choice(list(REGIONS))
        template = t(category)
        if "{r}" in template and not has(cfg, "shipping"):
            return False  # câu hỏi phí ship cho shop không có tài liệu ship: đã có ở missing_info
        if "doi tra" in template or "đổi trả" in template:
            if not has(cfg, "returns"):
                return False
        if ("mo lai" in template or "con lam" in template) and not has(cfg, "hours"):
            return False  # hỏi giờ mở cửa mà shop chưa viết giờ: đã có ở missing_info
        msg = b.fill(template, shop, p, region=region)
        exp = {
            "intents_any": ["faq", "product_info", "price_quote", "other"],
            "policy": {"situation": category, "run_agent": True},
        }
        notes = f"shop.json hours: thời điểm này {category}"
        if "{r}" in template:
            exp["amounts"], where = topic_answer(cfg, "ship", region)
            notes += f"; {doc_name(cfg)} {where}"
        elif "{p}" in template:
            exp["sources_include"] = [{"type": "product", "sku": p["sku"]}]
            notes += f"; sản phẩm {p['sku']}"
            if "giá" in template:
                exp["amounts"] = [p["price"]]
                notes += f" giá {money(p['price'])}"
        return b.add(shop, category, persona, [msg], exp, notes + ".", difficulty="medium")

    if category == "hard_negative":
        p = rng.choice(products)
        template = t("hard_negative")
        if "chất liệu" in template and not p["attrs"]["material"]:
            return False
        msg = b.fill(template, shop, p)
        exp = {
            "intents_any": ["product_info", "price_quote"],
            "sources_include": [{"type": "product", "sku": p["sku"]}],
            "policy": no_policy(),
        }
        if "giá" in template:
            exp["amounts"] = [p["price"]]
        return b.add(
            shop,
            category,
            persona,
            msg.split("\n"),
            exp,
            f"Khách bình thường (dấu !!/??, chữ 'không', nhiều tin) hỏi {p['sku']}; "
            "không cần người.",
            difficulty="hard",
        )

    if category == "multi_turn":
        if len(products) < 2:
            return False
        p, p2 = rng.sample(products, 2)
        multi = len({x["color"] for x in p["variants"]}) > 1
        v1, v2 = (
            rng.sample(p["variants"], 2)
            if len(p["variants"]) > 1
            else (p["variants"][0], p["variants"][0])
        )
        l1 = variant_label(p["axis"], v1["size"], v1["color"], multi)
        l2 = variant_label(p["axis"], v2["size"], v2["color"], multi)
        final_kind = rng.choice(["ship", "price"]) if has(cfg, "shipping") else "price"
        region = rng.choice(list(REGIONS))
        opener = b.fill(rng.choice(MULTI_TURN_OPENERS), shop, p)
        history = [
            {"who": "khach", "text": style(opener, persona, rng)},
            {
                "who": "shop",
                "text": f"Dạ {p['short']} giá {money(p['price'])}, hiện {l1} "
                f"{'còn hàng' if v1['stock'] else 'đang hết'} ạ.",
            },
            {
                "who": "khach",
                "text": style(rng.choice(MULTI_TURN_FOLLOWUPS).format(v2=l2), persona, rng),
            },
            {"who": "shop", "text": f"Dạ {l2} {'còn hàng' if v2['stock'] else 'đang hết'} ạ."},
        ]
        final = rng.choice(MULTI_TURN_FINALS[final_kind])
        if final_kind == "ship":
            msg = b.fill(final, shop, p, region=region)
            amounts, where = topic_answer(cfg, "ship", region)
            exp = {"intents_any": ["faq"], "amounts": amounts, "policy": no_policy()}
            notes = f"Câu cuối hỏi phí ship: {doc_name(cfg)} {where}."
        else:
            msg = final.format(p2=p2["short"])
            exp = {
                "intents_any": ["price_quote"],
                "tools_any": ["search_products"],
                "sources_include": [{"type": "product", "sku": p2["sku"]}],
                "amounts": [p2["price"]],
                "policy": no_policy(),
            }
            notes = (
                f"Câu cuối đổi sang {p2['sku']}: giá {money(p2['price'])}; "
                f"không lẫn với {p['sku']}."
            )
        return b.add(shop, category, persona, [msg], exp, notes, history=history, difficulty="hard")

    if category == "frustration":
        first, last, reason = rng.choice(FRUSTRATION_TOPICS)
        p = rng.choice(products)
        order = rng.choice(w["orders"])
        multi = len({x["color"] for x in p["variants"]}) > 1
        v = rng.choice(p["variants"])
        values = {
            "o": order["id"],
            "p": p["short"],
            "v": variant_label(p["axis"], v["size"], v["color"], multi),
        }
        q1 = first.format(**{k: values[k] for k in values if f"{{{k}}}" in first})
        q_last = last.format(**{k: values[k] for k in values if f"{{{k}}}" in last})
        history = [
            {"who": "khach", "text": q1},
            {"who": "shop", "text": rng.choice(SHOP_STALLS)},
            {"who": "khach", "text": rng.choice(["Shop ơi?", "sao im vậy shop", "Có ai không ạ"])},
            {"who": "shop", "text": rng.choice(SHOP_STALLS)},
        ]
        return b.add(
            shop,
            category,
            persona,
            [q_last],
            {
                "intents_any": ["complaint", "human_request", "other"],
                "needs_human": True,
                "reason": reason,
                "escalation": {"frustration": "high", "human_decision": True},
                "policy": no_policy(),
            },
            "Khách hỏi lại câu đã hỏi sau hai lượt shop chưa trả lời: cần người.",
            history=history,
            difficulty="hard",
        )
    raise ValueError(category)


def build_tasks(rng: random.Random, world: dict) -> list[dict]:
    b = Builder(rng, world)
    shops = list(SHOPS)
    for ci, category in enumerate(CATEGORIES):
        made, tries = 0, 0
        want = COUNT.get(category, PER_CATEGORY)
        while made < want:
            tries += 1
            if tries > want * 200:
                raise RuntimeError(f"{category}: only {made} unique tasks")
            shop = shops[(ci + made + tries) % len(shops)]
            persona = rng.choice(PERSONA_BY_CATEGORY[category])
            if make_task(b, category, shop, persona):
                b.tasks[-1]["id"] = f"{shop}-{category}-{made + 1:03d}"
                made += 1
    rng.shuffle(b.tasks)
    return b.tasks


# ------------------------------------------------------------------------------ seeds
def toml_value(value: object) -> str:
    if isinstance(value, dict):
        items = ", ".join(f"{json.dumps(k)} = {toml_value(v)}" for k, v in value.items())
        return "{ " + items + " }"
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(x) for x in value) + "]"
    if value is None:
        return '""'
    if isinstance(value, bool):
        return str(value).lower()
    return json.dumps(value, ensure_ascii=False)


def write_eval_seeds(tasks: list[dict]) -> None:
    """40 ca: 2 ca mỗi nhóm + ca nhiều lượt; thứ tự theo id cho ổn định."""
    ordered = sorted(tasks, key=lambda x: x["id"])
    picked = []
    for category in CATEGORIES:
        picked += [x for x in ordered if x["category"] == category][:2]
    extra = [x for x in ordered if x["history"] and x not in picked][: 40 - len(picked)]
    lines = ["# 40 ca chọn từ backend/evals/sim_shop/tasks.json (2 ca/nhóm + ca nhiều lượt).", ""]
    for x in picked + extra:
        lines += [
            "[[cases]]",
            f'id = "{x["id"]}"',
            f'shop = "{x["shop"]}"',
            f'category = "{x["category"]}"',
            f"history = {toml_value(x['history'])}",
            f"messages = {toml_value(x['messages'])}",
            f"expected = {toml_value(x['expected'])}",
            "",
        ]
    target = ROOT.parents[1] / "src/app/modules/agent_management/templates/evals"
    target.mkdir(parents=True, exist_ok=True)
    (target / "faq_sales_quote.v1.toml").write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------------------- main
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    for old in (ROOT / "shops").glob("*/*"):  # shop đã đổi cấu hình có thể bớt file
        old.unlink()
    world = {}
    for key, cfg in SHOPS.items():
        folder = ROOT / "shops" / key
        folder.mkdir(parents=True, exist_ok=True)
        products = build_catalog(cfg, rng)
        hours = {
            "timezone": "Asia/Ho_Chi_Minh",
            "weekly": cfg["weekly"],
            "closed_days": cfg["closed_days"],
            "pause_until": PAUSE_UNTIL,
        }
        policy, faq = knowledge(cfg)
        customers, orders = build_people(cfg, key, products, rng)
        dump(
            folder / "shop.json",
            {
                "shop_key": key,
                "name": cfg["name"],
                "industry": cfg["industry"],
                "hours": hours,
                "product_types": [x[0] for x in cfg["pool"]],
                "knowledge": {
                    "sections": list(cfg["sections"]),
                    "policy_file": policy is not None,
                    "faq_count": len(faq),
                    "catalog_source": cfg["source"],
                    "skus": len(products),
                },
                "hidden_truth": {  # sự thật shop biết nhưng có thể CHƯA viết ra: không phải căn cứ
                    "shipping_vnd": dict(zip(REGIONS, cfg["ship"], strict=True)),
                    "freeship_from_vnd": cfg["freeship_from"],
                    "cod": cfg["cod"],
                    "cod_fee_vnd": cfg["cod_fee"],
                    "return_days": cfg["return_days"],
                    "wholesale_from": cfg["wholesale_from"],
                },
            },
        )
        if policy:
            (folder / "policy.md").write_text(policy, encoding="utf-8")
        if faq:
            dump(folder / "faq.json", faq)
        write_catalog(folder, cfg, products)
        dump(folder / "customers.json", customers)
        dump(folder / "orders.json", orders)
        world[key] = {"products": products, "orders": orders, "hours": hours}
    tasks = build_tasks(rng, world)
    dump(ROOT / "tasks.json", tasks)
    dump(ROOT / "personas.json", PERSONAS)
    write_eval_seeds(tasks)


if __name__ == "__main__":
    main()
