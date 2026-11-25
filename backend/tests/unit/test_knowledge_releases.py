from app.modules.knowledge.domain.releases import (
    KnowledgeSnapshot,
    SnapshotItem,
    build_snapshot,
    render_knowledge,
)


def test_snapshot_order_and_roundtrip_are_deterministic():
    f1 = SnapshotItem("b", "Phí ship?", "30.000đ", ("shipping",))
    f2 = SnapshotItem("a", "Đổi trả?", "7 ngày", None)
    d1 = SnapshotItem("d1", "Bảo hành", "12 tháng", ("warranty",))

    snap_a = build_snapshot([f1, f2], [d1])
    snap_b = build_snapshot([f2, f1], [d1])

    assert [e.id for e in snap_a.faqs] == ["a", "b"]
    assert snap_a == snap_b
    assert KnowledgeSnapshot.from_dict(snap_a.as_dict()) == snap_a

    r1 = render_knowledge(snap_a)
    r2 = render_knowledge(KnowledgeSnapshot.from_dict(snap_b.as_dict()))
    assert r1 == r2
    assert r1.sources == frozenset({("faq", "a"), ("faq", "b"), ("doc", "d1")})
    assert '<faq id="a">\nQ: Đổi trả?\nA: 7 ngày\n</faq>' in r1.block
    assert '<doc id="d1" title="Bảo hành">\n12 tháng\n</doc>' in r1.block


def test_hash_changes_when_snapshot_changes():
    s1 = build_snapshot([SnapshotItem("1", "Giá?", "350.000đ")], [])
    s2 = build_snapshot([SnapshotItem("1", "Giá?", "320.000đ")], [])
    assert render_knowledge(s1).hash != render_knowledge(s2).hash
