"""Parse CSV catalog (một dòng mỗi biến thể) thành sản phẩm theo SKU; báo mọi lỗi kèm số dòng."""

import csv
import io
from dataclasses import dataclass, field

REQUIRED = ("sku", "name", "description", "price_vnd", "size", "color", "stock")
MAX_ROWS = 5000
MAX_ERRORS = 20


class CatalogCsvError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class ParsedItem:
    sku: str
    name: str
    description: str | None
    price_vnd: int
    variants: list[dict] = field(default_factory=list)


def _int(value: str, column: str, line: int, errors: list[str]) -> int | None:
    try:
        number = int(value.strip())
    except ValueError:
        errors.append(f"line {line}: {column} must be a whole number")
        return None
    if number < 0:
        errors.append(f"line {line}: {column} must be >= 0")
        return None
    return number


def parse_catalog_csv(content: str) -> list[ParsedItem]:
    reader = csv.DictReader(io.StringIO(content.removeprefix("﻿")))
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise CatalogCsvError([f"missing columns: {', '.join(missing)}"])

    items: dict[str, ParsedItem] = {}
    errors: list[str] = []
    rows = 0
    for line, row in enumerate(reader, start=2):  # dòng 1 là header
        rows += 1
        if rows > MAX_ROWS:
            raise CatalogCsvError([f"too many rows (max {MAX_ROWS})"])
        sku, name = (row["sku"] or "").strip(), (row["name"] or "").strip()
        if not sku:
            errors.append(f"line {line}: sku is required")
        if not name:
            errors.append(f"line {line}: name is required")
        price = _int(row["price_vnd"] or "", "price_vnd", line, errors)
        stock = _int(row["stock"] or "", "stock", line, errors)
        if not sku or not name or price is None or stock is None:
            continue

        description = (row["description"] or "").strip() or None
        item = items.get(sku)
        if item is None:
            item = items[sku] = ParsedItem(sku, name, description, price)
        elif (item.name, item.price_vnd) != (name, price):
            errors.append(f"line {line}: sku {sku} has a different name or price than before")
            continue
        variant = {
            "size": (row["size"] or "").strip() or None,
            "color": (row["color"] or "").strip() or None,
            "stock": stock,
        }
        if any(
            (v["size"], v["color"]) == (variant["size"], variant["color"]) for v in item.variants
        ):
            errors.append(f"line {line}: duplicate variant for sku {sku}")
            continue
        item.variants.append(variant)

    if errors:
        raise CatalogCsvError(errors[:MAX_ERRORS])
    if not items:
        raise CatalogCsvError(["no data rows"])
    return list(items.values())
