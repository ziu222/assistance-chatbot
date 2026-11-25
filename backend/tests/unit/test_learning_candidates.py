from app.modules.learning.domain.candidates import (
    EvidenceItem,
    check_candidate,
    detect_pii,
    fingerprint,
    has_customer_overlap,
    has_enough_evidence,
    is_significant,
    strip_pii,
    user_turn,
)


def _ev(
    *,
    action: str = "edited",
    diff_features: dict | None = None,
    staff_text: str = "Áo khoác gió hiện giảm còn 320.000đ ạ.",
) -> EvidenceItem:
    return EvidenceItem(
        draft_id="d1",
        conversation_id="c1",
        message_id="m1",
        intent="price_quote",
        topic="shipping",
        action=action,
        diff_features=diff_features or {},
        staff_text=staff_text,
    )


def test_significance_and_enough_evidence_rules():
    minor = _ev(diff_features={"sentences_added": 0})
    assert not is_significant(minor)

    edit_price_1 = _ev(diff_features={"amounts_added": [320000], "amounts_removed": [350000]})
    edit_price_2 = _ev(diff_features={"amounts_added": [320000], "amounts_removed": [350000]})
    edit_other = _ev(diff_features={"amounts_added": [290000], "amounts_removed": [350000]})

    assert not has_enough_evidence([edit_price_1])
    assert has_enough_evidence([edit_price_1, edit_price_2])
    assert not has_enough_evidence([edit_price_1, edit_other])

    short_discard = _ev(action="discarded", staff_text="Dạ vâng ạ")
    real_discard = _ev(action="discarded", staff_text="Shop hỗ trợ đổi size trong 7 ngày ạ.")
    assert not is_significant(short_discard)
    assert is_significant(real_discard)
    assert has_enough_evidence([real_discard, real_discard])


def test_pii_detection_and_stripping():
    raw = "Gọi 0912 345 678 hoặc mail a@b.vn cho đơn DH123456 ở số 12 đường Lê Lợi quận 1."
    assert set(detect_pii(raw)) == {"phone", "email", "order_code", "address"}
    cleaned, removed = strip_pii(raw)
    assert "0912" not in cleaned
    assert "a@b.vn" not in cleaned
    assert "DH123456" not in cleaned
    assert detect_pii(cleaned) == []
    assert set(removed) == {"phone", "email", "order_code", "address"}


def test_customer_overlap_blocks_more_than_8_matching_words():
    customer = ["em muon hoi ao khoac gio mau den size m gia bao nhieu tien vay shop oi"]
    nine_words = "ao khoac gio mau den size m gia bao"
    eight_words = "ao khoac gio mau den size m gia"
    assert has_customer_overlap(f"Câu hỏi về {nine_words} nhé", customer)
    assert not has_customer_overlap(f"Câu hỏi về {eight_words} nhé", customer)


def test_check_candidate_verifies_facts_pii_and_customer_text():
    staff = ["Shop miễn phí giao nội thành, giao tỉnh 30.000đ trong 3 ngày ạ."]
    customer = ["shop oi cho em hoi phi giao hang ve tinh la bao nhieu ngay a"]

    ok = check_candidate(
        "Phí giao tỉnh là 30.000đ, nhận hàng trong 3 ngày.",
        staff_texts=staff,
        customer_texts=customer,
    )
    assert ok.passed

    bad_amount = check_candidate(
        "Phí giao tỉnh là 20.000đ, nhận hàng trong 3 ngày.",
        staff_texts=staff,
        customer_texts=customer,
    )
    assert not bad_amount.passed
    assert bad_amount.ungrounded_facts == {"amounts": ["20000"]}

    bad_customer = check_candidate(
        "shop oi cho em hoi phi giao hang ve tinh la bao nhieu",
        staff_texts=staff,
        customer_texts=customer,
    )
    assert not bad_customer.passed
    assert bad_customer.customer_overlap


def test_user_turn_and_fingerprint():
    turn = user_turn("price_quote", "shipping", ["Ship 30k"], [("id-1", "Ship?", "35k")])
    assert "Ship 30k" in turn and 'id="id-1"' in turn
    assert fingerprint("faq_update", "price_quote", "shipping", "Phí Ship 30k") == fingerprint(
        "faq_update", "price_quote", "shipping", "phi ship 30k"
    )
