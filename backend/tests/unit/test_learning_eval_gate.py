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
    from app.modules.learning.domain.eval_gate import (
        EvalCaseSpec,
        build_standard_amount_case,
        still_valid,
    )

    item = SnapshotItem("f1", "Giá áo?", "320.000đ")
    case = EvalCaseSpec.from_row("c1", "Giá áo?", {}, entry_id="f1", entry_hash=entry_hash(item))
    assert still_valid(case, build_snapshot([item], []))
    assert not still_valid(case, build_snapshot([SnapshotItem("f1", "Giá áo?", "300.000đ")], []))
    assert not still_valid(case, build_snapshot([], []))
    assert still_valid(EvalCaseSpec.from_row("c2", "Q", {}), build_snapshot([], []))

    # Ca chuẩn kiểm số tiền phải gắn entry_id + entry_hash; đổi phí ship ⇒ tự hết hiệu lực.
    ship = SnapshotItem("f-ship", "Phí ship?", "30.000đ toàn quốc")
    std_amt = build_standard_amount_case("faq", ship, case_id="std-ship")
    assert std_amt.entry_id == "f-ship" and std_amt.entry_hash == entry_hash(ship)
    assert still_valid(std_amt, build_snapshot([ship], []))
    assert not still_valid(
        std_amt, build_snapshot([SnapshotItem("f-ship", "Phí ship?", "35.000đ toàn quốc")], [])
    )

    # Ca chuẩn kiểm số tiền mà KHÔNG gắn entry_id + entry_hash ⇒ luôn bị bỏ qua (chống hạ cấp oan).
    unbound = EvalCaseSpec(
        case_id="std-bad",
        question="Phí ship?",
        must_include_amounts=(30000,),
        source="standard",
        intent="faq",
        check_behavior=True,
    )
    assert not still_valid(unbound, build_snapshot([ship], []))


def test_build_standard_cases_are_behavioral_per_intent():
    from app.modules.knowledge.domain.releases import SnapshotItem, build_snapshot
    from app.modules.learning.domain.eval_gate import build_standard_cases

    snap = build_snapshot(
        [
            SnapshotItem("f1", "Giờ mở cửa?", "8h - 21h hằng ngày"),
            SnapshotItem("f2", "Phí ship?", "30.000đ"),
        ],
        [],
    )
    specs = build_standard_cases(
        ["faq", "greeting", "product_info"], snap, catalog_names=["Áo khoác gió"]
    )
    by_intent: dict[str, list] = {}
    for s in specs:
        assert s.source == "standard"
        assert s.check_behavior is True
        assert s.must_include_amounts == ()  # không khóa cứng số tiền vào ca động
        by_intent.setdefault(s.intent, []).append(s)

    assert len(by_intent["greeting"]) == 2
    assert all(not s.require_sources and not s.allow_needs_human for s in by_intent["greeting"])
    assert len(by_intent["faq"]) == 2
    assert all(s.require_sources and not s.allow_needs_human for s in by_intent["faq"])
    assert len(by_intent["product_info"]) == 1


def test_grade_standard_reply_checks_sources_amounts_handoff_and_safety():
    from app.modules.learning.domain.eval_gate import (
        EvalCaseSpec,
        EvalDraftOut,
        EvalSource,
        grade_standard_reply,
        regressed_intents,
    )

    faq_spec = EvalCaseSpec(
        case_id="std:faq:f1",
        question="Phí ship?",
        source="standard",
        intent="faq",
        check_behavior=True,
        require_sources=True,
        allow_needs_human=False,
    )
    greet_spec = EvalCaseSpec(
        case_id="std:greeting:1",
        question="Xin chào shop!",
        source="standard",
        intent="greeting",
        check_behavior=True,
        require_sources=False,
        allow_needs_human=False,
    )
    valid_sources = {("faq", "f1")}
    known_amounts = {30000}

    ok_faq = grade_standard_reply(
        faq_spec,
        EvalDraftOut(
            reply="Dạ phí ship là 30.000đ ạ.",
            sources=[EvalSource(type="faq", id="f1")],
            needs_human=False,
        ),
        valid_sources=valid_sources,
        known_amounts=known_amounts,
    )
    assert ok_faq.passed and ok_faq.intent == "faq"

    # Không nguồn
    no_src = grade_standard_reply(
        faq_spec,
        EvalDraftOut(reply="Dạ phí ship 30.000đ ạ.", sources=[], needs_human=False),
        valid_sources=valid_sources,
        known_amounts=known_amounts,
    )
    assert not no_src.passed and "missing_source" in no_src.reasons

    # Nguồn bịa
    bogus_src = grade_standard_reply(
        faq_spec,
        EvalDraftOut(
            reply="Dạ phí ship 30.000đ ạ.",
            sources=[EvalSource(type="faq", id="f999")],
            needs_human=False,
        ),
        valid_sources=valid_sources,
        known_amounts=known_amounts,
    )
    assert not bogus_src.passed and "source_not_provided" in bogus_src.reasons

    # Bịa số tiền
    hallucinated_amt = grade_standard_reply(
        faq_spec,
        EvalDraftOut(
            reply="Dạ phí ship 99.000đ ạ.",
            sources=[EvalSource(type="faq", id="f1")],
            needs_human=False,
        ),
        valid_sources=valid_sources,
        known_amounts=known_amounts,
    )
    assert not hallucinated_amt.passed and "ungrounded_amount:99000" in hallucinated_amt.reasons

    # Chuyển người thừa
    over_escalate = grade_standard_reply(
        greet_spec,
        EvalDraftOut(reply="Để nhân viên hỗ trợ bạn nhé.", sources=[], needs_human=True),
        valid_sources=valid_sources,
        known_amounts=known_amounts,
    )
    assert not over_escalate.passed and "unnecessary_handoff" in over_escalate.reasons

    # Chỉ ý định hỏng mới nằm trong regressed_intents
    assert regressed_intents([ok_faq, over_escalate]) == ("greeting",)
