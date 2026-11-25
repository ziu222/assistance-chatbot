from app.modules.learning.domain.suggestions import (
    BrandUseItem,
    OrderSignalItem,
    pick_brand_examples,
    suggest_order_flow_fields,
)


def test_pick_brand_examples_requires_repeated_clean_as_is_uses():
    text = "Dạ em cảm ơn anh/chị đã quan tâm sản phẩm bên shop ạ!"
    items = [BrandUseItem(f"d{i}", f"c{i}", f"m{i}", "greeting", text) for i in range(3)] + [
        BrandUseItem("dx", "cx", "mx", "greeting", "Chào chị"),  # quá ngắn
    ]

    picked = pick_brand_examples(items, existing_samples=[])
    assert len(picked) == 1
    assert picked[0].sample == text
    assert len(picked[0].evidence) == 3

    # Đã có trong BrandProfile.samples ⇒ không đề xuất lại
    assert pick_brand_examples(items, existing_samples=[text]) == []


def test_suggest_order_flow_fields_detects_repeated_missing_fields():
    signals = [
        OrderSignalItem("c1", "o1", ("delivery_time", "note")),
        OrderSignalItem("c2", "o2", ("delivery_time",)),
        OrderSignalItem("c3", "o3", ("delivery_time", "phone")),
    ]
    suggestions = suggest_order_flow_fields(
        signals,
        current_required=["product", "variant", "quantity", "name", "phone", "address"],
    )
    assert len(suggestions) == 1
    assert suggestions[0].field == "delivery_time"
    assert suggestions[0].count == 3
    assert len(suggestions[0].evidence) == 3
