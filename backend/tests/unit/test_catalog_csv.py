import pytest

from app.modules.knowledge.domain.catalog_csv import CatalogCsvError, parse_catalog_csv
from app.modules.knowledge.domain.text import fold

HEADER = "sku,name,description,price_vnd,size,color,stock\n"


@pytest.mark.parametrize(
    ("raw", "folded"),
    [
        ("Áo Khoác Gió", "ao khoac gio"),
        ("ĐẦM dạ hội", "dam da hoi"),
        ("  quần   jean\tnữ ", "quan jean nu"),
        ("Giày thể thao NIKE-Air", "giay the thao nike-air"),
        ("", ""),
    ],
)
def test_fold_removes_vietnamese_marks(raw, folded):
    assert fold(raw) == folded


def test_groups_variant_rows_by_sku():
    items = parse_catalog_csv(
        HEADER
        + "AK-02,Áo khoác gió,Chống nước,350000,M,đen,5\n"
        + "AK-02,Áo khoác gió,Chống nước,350000,L,đen,0\n"
        + "Q-01,Quần jean,,420000,,,12\n"
    )
    assert [i.sku for i in items] == ["AK-02", "Q-01"]
    ak = items[0]
    assert (ak.name, ak.price_vnd, ak.description) == ("Áo khoác gió", 350000, "Chống nước")
    assert ak.variants == [
        {"size": "M", "color": "đen", "stock": 5},
        {"size": "L", "color": "đen", "stock": 0},
    ]
    assert items[1].variants == [{"size": None, "color": None, "stock": 12}]


def test_accepts_utf8_bom_and_crlf():
    items = parse_catalog_csv("﻿" + HEADER.replace("\n", "\r\n") + "A-1,Áo,,1000,,,1\r\n")
    assert items[0].sku == "A-1"


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("A-1,Áo,,abc,,,1\n", "line 2: price_vnd"),
        ("A-1,Áo,,-5,,,1\n", "line 2: price_vnd"),
        ("A-1,Áo,,1000,,,-1\n", "line 2: stock"),
        (",Áo,,1000,,,1\n", "line 2: sku"),
        ("A-1,,,1000,,,1\n", "line 2: name"),
        ("A-1,Áo,,1000,M,,1\nA-1,Áo khác,,1000,L,,1\n", "line 3: sku A-1"),  # tên/giá mâu thuẫn
        ("A-1,Áo,,1000,M,,1\nA-1,Áo,,1000,M,,2\n", "line 3: duplicate variant"),
    ],
)
def test_reports_errors_with_line_numbers(body, fragment):
    with pytest.raises(CatalogCsvError) as e:
        parse_catalog_csv(HEADER + body)
    assert any(fragment in err for err in e.value.errors), e.value.errors


def test_rejects_missing_columns_and_empty():
    with pytest.raises(CatalogCsvError) as e:
        parse_catalog_csv("sku,name\nA,B\n")
    assert "missing columns" in e.value.errors[0]
    with pytest.raises(CatalogCsvError):
        parse_catalog_csv(HEADER)  # không có dòng dữ liệu


def test_rejects_too_many_rows():
    rows = "".join(f"S{i},Áo,,1000,,,1\n" for i in range(5001))
    with pytest.raises(CatalogCsvError) as e:
        parse_catalog_csv(HEADER + rows)
    assert "too many rows" in e.value.errors[0]
