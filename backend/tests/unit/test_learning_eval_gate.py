from app.modules.knowledge.domain.releases import SnapshotItem, build_snapshot
from app.modules.learning.domain.eval_gate import (
    EvalCaseSpec,
    apply_candidate_to_snapshot,
    build_lesson_case,
    evaluate_gate,
    grade_reply,
)


def test_build_lesson_case_sets_new_and_forbidden_amounts():
    spec = build_lesson_case(
        {"question": "Giá áo khoác gió?", "answer": "Hiện giảm còn 320.000đ."},
        kind="faq_update",
        case_id="c-new",
        old_answer="Áo khoác gió giá 350.000đ.",
    )
    assert spec.is_new is True
    assert spec.must_include_amounts == (320000,)
    assert spec.forbidden_amounts == (350000,)


def test_grade_reply_and_evaluate_gate():
    new_case = build_lesson_case(
        {"question": "Giá áo khoác gió?", "answer": "320.000đ"},
        kind="faq_update",
        case_id="new",
        old_answer="350.000đ",
    )
    old_case = EvalCaseSpec(
        case_id="old",
        question="Phí ship?",
        must_include_amounts=(30000,),
        must_include_terms=("3 ngày",),
    )

    ok_new = grade_reply(new_case, "Áo khoác gió giá 320.000đ ạ.")
    ok_old = grade_reply(old_case, "Phí ship 30k, nhận hàng trong 3 ngày.")
    assert ok_new.passed and ok_old.passed
    assert evaluate_gate([ok_new, ok_old]).passed

    bad_new = grade_reply(new_case, "Áo khoác gió giá 350.000đ ạ.")
    assert not bad_new.passed
    assert "missing_amount:320000" in bad_new.reasons
    assert "forbidden_amount:350000" in bad_new.reasons
    decision = evaluate_gate([bad_new, ok_old])
    assert not decision.passed
    assert len(decision.failed_cases) == 1


def test_apply_candidate_to_snapshot_replaces_or_adds():
    base = build_snapshot([SnapshotItem("f1", "Giá cũ?", "350.000đ")], [])
    updated = apply_candidate_to_snapshot(
        base,
        kind="faq_update",
        proposal={"question": "Giá mới?", "answer": "320.000đ"},
        replaces="f1",
        item_id="unused",
    )
    assert len(updated.faqs) == 1
    assert updated.faqs[0].id == "f1"
    assert updated.faqs[0].second == "320.000đ"

    added = apply_candidate_to_snapshot(
        updated,
        kind="doc_section",
        proposal={"title": "Đổi trả", "body": "Trong 7 ngày"},
        replaces=None,
        item_id="d1",
        topic="return_policy",
    )
    assert len(added.docs) == 1
    assert added.docs[0].id == "d1"
    assert added.docs[0].topics == ("return_policy",)


def test_still_valid_follows_entry_content():
    from app.modules.knowledge.domain.releases import SnapshotItem, build_snapshot, entry_hash
    from app.modules.learning.domain.eval_gate import EvalCaseSpec, still_valid

    item = SnapshotItem("f1", "Giá áo?", "320.000đ")
    case = EvalCaseSpec.from_row("c1", "Giá áo?", {}, entry_id="f1", entry_hash=entry_hash(item))
    assert still_valid(case, build_snapshot([item], []))
    assert not still_valid(case, build_snapshot([SnapshotItem("f1", "Giá áo?", "300.000đ")], []))
    assert not still_valid(case, build_snapshot([], []))
    assert still_valid(EvalCaseSpec.from_row("c2", "Q", {}), build_snapshot([], []))
