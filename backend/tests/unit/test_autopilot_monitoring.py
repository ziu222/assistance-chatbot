from app.modules.outbound.domain.monitoring import (
    classify_staff_correction,
    demotion_reason,
    is_repeat_question,
)


def test_is_repeat_question_detects_near_identical_follow_up():
    assert is_repeat_question("Shop mở cửa mấy giờ vậy ạ?", "shop mo cua may gio vay a")
    assert not is_repeat_question("Shop mở cửa mấy giờ?", "Phí ship Đà Nẵng bao nhiêu?")
    assert not is_repeat_question("ok", "ok")  # quá ngắn


def test_classify_staff_correction_flags_amount_changes():
    changed = classify_staff_correction(
        "Dạ áo khoác gió giá 350.000đ ạ.",
        "Xin lỗi chị, áo khoác gió hiện giá 320.000đ nhé.",
    )
    assert changed["amount_changed"] is True
    assert changed["diff_features"]["amounts_added"] == [320000]
    assert changed["diff_features"]["amounts_removed"] == [350000]

    same = classify_staff_correction(
        "Dạ shop mở cửa từ 8h đến 21h ạ.",
        "Dạ cuối tuần shop cũng mở bình thường nhé chị.",
    )
    assert same["amount_changed"] is False


def test_demotion_reason_thresholds():
    # An toàn dưới ngưỡng
    assert demotion_reason(50, staff_amount_corrections=1, customer_reactions=2) is None
    # Chủ shop gán nhãn sai sự thật ⇒ hạ cấp ngay
    assert demotion_reason(10, owner_wrong_fact=1) == "owner_label_wrong"
    # Lệch số tiền sau gửi ⇒ hạ cấp ngay
    assert demotion_reason(10, amount_conflicts=1) == "amount_conflict"
    # Đính chính đổi số tiền > 2% trên 50 tin (2/50 = 4%) ⇒ hạ cấp ngay
    assert demotion_reason(50, staff_amount_corrections=2) == "staff_correction_rate"
    assert demotion_reason(10, staff_amount_corrections=2) == "staff_correction_rate"
    # Khách phản ứng xấu > 5% trên 50 tin (3/50 = 6%) ⇒ hạ cấp ngay
    assert demotion_reason(50, customer_reactions=3) == "customer_reaction_rate"
