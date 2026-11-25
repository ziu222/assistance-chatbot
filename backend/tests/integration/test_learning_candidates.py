"""Dựng ứng viên tri thức từ bản ghi sửa của nhân viên (thiết kế 05 L3)."""

from uuid import uuid4

from sqlalchemy import text

from app.modules.agent_runtime.llm.fake import FakeLLM
from app.modules.knowledge.infrastructure.releases import publish_from_tables
from app.modules.learning.domain.candidates import CandidateOut
from app.modules.learning.infrastructure.candidates import LearningWorker, build_candidates_once

from .test_agent_runtime import result
from .test_learning_drafts import send, shop


def _learning_llm(out: CandidateOut) -> FakeLLM:
    return FakeLLM([result(out)])


async def test_two_consistent_price_edits_create_candidate_and_respect_cap(
    app_client, session_factory
):
    w, conv1, client = await shop(app_client, session_factory, mode="learning")
    async with session_factory() as s, s.begin():
        faq_id = uuid4()
        await s.execute(
            text(
                "INSERT INTO faq_entries (id, tenant_id, question, answer, status) "
                "VALUES (:id, :t, 'Giá áo khoác gió?', 'Áo khoác gió giá 350.000đ', 'approved')"
            ),
            {"id": faq_id, "t": w.ctx.tenant_id},
        )
        await publish_from_tables(s, w.ctx.tenant_id, reason="manual_edit", created_by=None)

    # Lần sửa 1
    d1 = (await client.get(f"/api/v1/conversations/{conv1}/draft")).json()["draft"]["id"]
    await send(client, conv1, "Dạ áo khoác gió hiện giảm còn 320.000đ ạ.", draft_id=d1)

    # Chỉ 1 lần sửa ⇒ chưa đủ bằng chứng
    llm_unused = _learning_llm(
        CandidateOut(
            kind="faq_update",
            title="Giá áo khoác gió bao nhiêu?",
            body="Áo khoác gió hiện có giá 320.000đ.",
            replaces_id=str(faq_id),
        )
    )
    assert await build_candidates_once(session_factory, llm_unused, daily_cap_micro=500_000) == 0
    assert len(llm_unused.requests) == 0

    # Tạo thêm 1 bản ghi sửa thứ 2 cùng hướng (350k -> 320k) trong cùng tenant
    async with session_factory() as s, s.begin():
        run1 = (
            await s.execute(
                text(
                    "SELECT agent_id, agent_version_id, template_key, template_version, "
                    "       prompt_version, knowledge_hash, intents "
                    "FROM agent_runs WHERE tenant_id = :t LIMIT 1"
                ),
                {"t": w.ctx.tenant_id},
            )
        ).one()
        run2_id = uuid4()
        await s.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, tenant_id, agent_id, agent_version_id, conversation_id, from_sequence, "
                " to_sequence, status, template_key, template_version, prompt_version, "
                " knowledge_hash, intents) "
                "VALUES (:id, :t, :aid, :avid, :c, 2, 2, 'completed', :tk, :tv, :pv, :kh, :intents)"
            ),
            {
                "id": run2_id,
                "t": w.ctx.tenant_id,
                "aid": run1.agent_id,
                "avid": run1.agent_version_id,
                "c": conv1,
                "tk": run1.template_key,
                "tv": run1.template_version,
                "pv": run1.prompt_version,
                "kh": run1.knowledge_hash,
                "intents": run1.intents,
            },
        )
        draft2 = uuid4()
        await s.execute(
            text(
                "INSERT INTO agent_drafts "
                "(id, tenant_id, run_id, conversation_id, based_on_sequence, text, sources, "
                " grounded, needs_human) "
                "VALUES (:id, :t, :r, :c, 2, 'Dạ áo khoác gió giá 350.000đ ạ', '[]', true, false)"
            ),
            {"id": draft2, "t": w.ctx.tenant_id, "r": run2_id, "c": conv1},
        )
    await send(client, conv1, "Áo khoác gió đang bán 320.000đ nhé chị.", draft_id=str(draft2))

    # Trần chi phí = 0 ⇒ bỏ qua, không gọi LLM
    assert await build_candidates_once(session_factory, llm_unused, daily_cap_micro=0) == 0
    assert len(llm_unused.requests) == 0

    # Đủ bằng chứng + còn ngân sách ⇒ sinh ứng viên faq_update đã qua kiểm tra
    worker = LearningWorker(session_factory, llm_unused, daily_cap_micro=500_000)
    assert await worker.process_once() == 1
    assert len(llm_unused.requests) == 1

    async with session_factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT kind, replaces, proposal, evidence, checks, status "
                    "FROM knowledge_candidates WHERE tenant_id = :t"
                ),
                {"t": w.ctx.tenant_id},
            )
        ).one()
    assert row.kind == "faq_update"
    assert row.replaces == faq_id
    assert row.proposal == {
        "question": "Giá áo khoác gió bao nhiêu?",
        "answer": "Áo khoác gió hiện có giá 320.000đ.",
    }
    assert len(row.evidence) == 2
    assert row.checks["passed"] is True
    assert row.status == "pending"

    # Chạy lại lần nữa ⇒ không tạo trùng ứng viên
    assert await worker.process_once() == 0
