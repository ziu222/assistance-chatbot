"""Integration tests: đưa `brand_profiles` vào khối `<brand_voice>` ở bước soạn nháp."""

from uuid import uuid4

from app.modules.authoring.domain.writing import BrandProfile
from app.modules.authoring.infrastructure.repository import save_profile

from .test_agent_runtime import classify, draft_out, make_runner, make_world, request_for


async def test_draft_prompt_includes_brand_voice_but_classify_does_not(session_factory):
    w = await make_world(session_factory)
    async with session_factory() as s, s.begin():
        await save_profile(
            s,
            w.tenant_id,
            BrandProfile(
                shop_pronoun="mình",
                customer_pronoun="bạn",
                tone="tre_trung",
                banned_terms=["kính thưa"],
            ),
            uuid4(),
        )
    runner, llm = make_runner(
        session_factory,
        [
            classify("faq"),
            draft_out("Mình giao toàn quốc nhé bạn!", sources=[("faq", str(w.faq_id))]),
        ],
    )
    req = await request_for(session_factory, w, ("shop có giao toàn quốc không?",))
    await runner.handle(req)

    assert len(llm.requests) == 2
    classify_blocks = "\n".join(llm.requests[0].system_blocks)
    draft_blocks = "\n".join(llm.requests[1].system_blocks)
    assert "<brand_voice>" not in classify_blocks
    assert "<brand_voice>" in draft_blocks
    assert 'xưng "mình", gọi khách "bạn"' in draft_blocks
    assert "kính thưa" in draft_blocks


async def test_draft_prompt_omits_brand_voice_when_tenant_has_no_profile(session_factory):
    w = await make_world(session_factory)
    runner, llm = make_runner(
        session_factory,
        [
            classify("faq"),
            draft_out("Dạ shop giao toàn quốc ạ.", sources=[("faq", str(w.faq_id))]),
        ],
    )
    req = await request_for(session_factory, w, ("shop có giao toàn quốc không?",))
    await runner.handle(req)

    assert len(llm.requests) == 2
    assert "<brand_voice>" not in "\n".join(llm.requests[1].system_blocks)
