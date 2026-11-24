"""Hiệu chỉnh giám khảo với nhãn chủ shop (thiết kế 04 §6.3)."""

import pytest

from app.modules.shadow.domain.calibration import calibrate, cohen_kappa, judge_label


def test_judge_scale_maps_onto_owner_labels():
    assert [judge_label(s) for s in ("as_is", "minor_edit", "rewrite", "must_not_send")] == [
        "sendable",
        "sendable",
        "rewrite",
        "wrong",
    ]


def test_kappa_known_values():
    assert cohen_kappa([]) is None
    assert cohen_kappa([("sendable", "sendable"), ("wrong", "wrong")]) == 1.0
    # Ví dụ sách giáo khoa: khớp 70%, kỳ vọng ngẫu nhiên 50% ⇒ kappa 0,4.
    pairs = [("a", "a")] * 35 + [("a", "b")] * 15 + [("b", "a")] * 15 + [("b", "b")] * 35
    assert cohen_kappa(pairs) == pytest.approx(0.4)
    assert cohen_kappa([("sendable", "sendable")] * 5) == 1.0  # luôn cùng một nhãn


def test_calibrated_needs_enough_labels_and_agreement():
    good = [("sendable", "sendable")] * 20 + [("wrong", "wrong")] * 10
    assert calibrate(good).calibrated
    assert not calibrate(good[:29]).calibrated  # dưới 30 nhãn
    noisy = [("sendable", "wrong")] * 15 + [("wrong", "sendable")] * 15
    c = calibrate(noisy)
    assert not c.calibrated and c.agreement == 0.0
    mixed = calibrate([("sendable", "sendable"), ("rewrite", "wrong")])
    assert mixed.agreement == 1.0  # rewrite và wrong đều là "không gửi được"
