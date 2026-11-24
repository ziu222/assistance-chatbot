"""REST so sánh shadow (thiết kế 04 §8): gán nhãn cho cặp đã chấm, mức hiệu chỉnh giám khảo."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict

from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import Csrf, CurrentStaff
from app.modules.shadow.domain.calibration import MIN_KAPPA, MIN_LABELS, Label
from app.modules.shadow.domain.judge import JUDGE_PROMPT_VERSION
from app.modules.shadow.infrastructure.labels import calibration, list_for_labelling, set_label
from app.modules.shadow.infrastructure.summary import summary
from app.shared.presentation.http_errors import ApiError

router = APIRouter(prefix="/api/v1/shadow")


def _require(actor: StaffActor, action: str) -> UUID:
    if not authorize(actor, action, actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    return actor.tenant_id


class LabelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Label


@router.get("/comparisons")
async def comparisons(
    request: Request,
    actor: CurrentStaff,
    intent: Annotated[str | None, Query(max_length=50)] = None,
    unlabelled: bool = True,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    # Nội dung khách và nháp: cùng quyền với người được gán nhãn.
    tenant_id = _require(actor, "shadow.label")
    async with request.app.state.session_factory() as s:
        items = await list_for_labelling(
            s, tenant_id, intent=intent, unlabelled=unlabelled, limit=limit
        )
    return {"items": items}


@router.post("/comparisons/{comparison_id}/label", dependencies=[Csrf])
async def label(comparison_id: UUID, body: LabelIn, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "shadow.label")
    async with request.app.state.session_factory() as s, s.begin():
        ok = await set_label(s, tenant_id, comparison_id, body.label, actor.user_id)
    if not ok:
        raise ApiError(404, "not_found", "Not found")
    return {"ok": True}


@router.get("/calibration")
async def get_calibration(request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "agent.read")
    async with request.app.state.session_factory() as s:
        c = await calibration(s, tenant_id)
    return {
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "labels": c.labels,
        "kappa": None if c.kappa is None else round(c.kappa, 3),
        "agreement": None if c.agreement is None else round(c.agreement, 3),
        "calibrated": c.calibrated,
        "needs": {"labels": MIN_LABELS, "kappa": MIN_KAPPA},
    }


@router.get("/summary")
async def get_summary(
    request: Request, actor: CurrentStaff, days: Annotated[int, Query(ge=1, le=90)] = 14
) -> dict:
    """Theo ý định: tỉ lệ gửi được/sai, chuyển người, bot nhanh hơn bao lâu, mức sẵn sàng."""
    tenant_id = _require(actor, "agent.read")
    async with request.app.state.session_factory() as s:
        return await summary(s, tenant_id, days)
