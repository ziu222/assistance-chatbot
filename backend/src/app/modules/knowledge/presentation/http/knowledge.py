"""REST knowledge (thiết kế bước 2 §8.1): FAQ, tài liệu nhỏ, import + thử tìm catalog."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import Csrf, CurrentStaff
from app.modules.knowledge.domain.catalog_csv import CatalogCsvError, parse_catalog_csv
from app.modules.knowledge.domain.releases import render_knowledge
from app.modules.knowledge.domain.topics import TOPICS, effective_topics
from app.modules.knowledge.infrastructure import content, releases
from app.modules.knowledge.infrastructure.catalog import (
    SyncResult,
    get_catalog_item,
    import_catalog,
    record_sync_run,
    search_catalog,
)
from app.modules.knowledge.infrastructure.catalog_sync import (
    get_source_config,
    recent_runs,
    save_source_config,
    sync_sheets,
)
from app.modules.knowledge.infrastructure.sheets import (
    CELL_RANGE,
    SPREADSHEET_ID,
    service_account_email,
)
from app.shared.presentation.http_errors import ApiError

MAX_CSV_BYTES = 2 * 1024 * 1024

router = APIRouter(prefix="/api/v1")


def _require(actor: StaffActor, action: str) -> UUID:
    if not authorize(actor, action, actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    return actor.tenant_id


# ---------- FAQ + tài liệu: cùng hình dạng, khác tên cột ----------

Topic = Literal[tuple(TOPICS)]  # type: ignore[valid-type]  # cùng danh sách với domain
# `topics`: bỏ trống = hệ thống đoán từ nội dung; gửi mảng (kể cả rỗng) = chủ shop chọn;
# PATCH với null = quay lại để hệ thống đoán.


class FaqIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topics: list[Topic] | None = None
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=4000)
    status: Literal["draft", "approved"] = "approved"


class FaqPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topics: list[Topic] | None = None
    question: str | None = Field(default=None, min_length=1, max_length=500)
    answer: str | None = Field(default=None, min_length=1, max_length=4000)
    status: Literal["draft", "approved", "archived"] | None = None


class DocIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topics: list[Topic] | None = None
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    status: Literal["draft", "approved"] = "approved"


class DocPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topics: list[Topic] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    status: Literal["draft", "approved", "archived"] | None = None


def _register(kind: content.Kind, path: str, names: tuple[str, str], create_model, patch_model):
    a, b = names

    def as_json(e: content.Entry) -> dict:
        return {
            "id": str(e.id),
            a: e.first,
            b: e.second,
            "status": e.status,
            "topics": e.topics,  # null = hệ thống đoán
            "effective_topics": effective_topics(e.topics, f"{e.first} {e.second}"),
            "created_at": e.created_at.isoformat(),
            "updated_at": e.updated_at.isoformat(),
        }

    @router.get(path, name=f"list_{kind}")
    async def list_(request: Request, actor: CurrentStaff) -> dict:
        tenant_id = _require(actor, "knowledge.read")
        async with request.app.state.session_factory() as s:
            return {"items": [as_json(e) for e in await content.list_entries(s, kind, tenant_id)]}

    @router.post(path, dependencies=[Csrf], name=f"create_{kind}")
    async def create(body: create_model, request: Request, actor: CurrentStaff) -> JSONResponse:
        tenant_id = _require(actor, "knowledge.manage")
        async with request.app.state.session_factory() as s, s.begin():
            entry = await content.create_entry(
                s, kind, tenant_id, getattr(body, a), getattr(body, b), body.status, body.topics
            )
            await releases.publish_from_tables(
                s, tenant_id, reason="manual_edit", created_by=actor.user_id
            )
        return JSONResponse(as_json(entry), status_code=201)

    @router.patch(path + "/{entry_id}", dependencies=[Csrf], name=f"update_{kind}")
    async def update(
        entry_id: UUID, body: patch_model, request: Request, actor: CurrentStaff
    ) -> dict:
        tenant_id = _require(actor, "knowledge.manage")
        async with request.app.state.session_factory() as s, s.begin():
            entry = await content.update_entry(
                s,
                kind,
                tenant_id,
                entry_id,
                first=getattr(body, a),
                second=getattr(body, b),
                status=body.status,
                topics=body.topics,
                set_topics="topics" in body.model_fields_set,
            )
            if entry is not None:
                await releases.publish_from_tables(
                    s, tenant_id, reason="manual_edit", created_by=actor.user_id
                )
        if entry is None:
            raise ApiError(404, "not_found", "Not found")
        return as_json(entry)

    @router.delete(
        path + "/{entry_id}", status_code=204, dependencies=[Csrf], name=f"archive_{kind}"
    )
    async def archive(entry_id: UUID, request: Request, actor: CurrentStaff) -> Response:
        # Lưu trữ thay vì xóa cứng: bản nháp cũ của agent vẫn trích nguồn tới id này.
        tenant_id = _require(actor, "knowledge.manage")
        async with request.app.state.session_factory() as s, s.begin():
            entry = await content.update_entry(
                s, kind, tenant_id, entry_id, first=None, second=None, status="archived"
            )
            if entry is not None:
                await releases.publish_from_tables(
                    s, tenant_id, reason="manual_edit", created_by=actor.user_id
                )
        if entry is None:
            raise ApiError(404, "not_found", "Not found")
        return Response(status_code=204)


_register("faq", "/knowledge/faqs", ("question", "answer"), FaqIn, FaqPatch)
_register("doc", "/knowledge/docs", ("title", "body"), DocIn, DocPatch)


# ---------- bản phát hành tri thức (thiết kế 05 L2) ----------


def _release_json(r: releases.KnowledgeRelease) -> dict:
    return {
        "id": str(r.id),
        "number": r.number,
        "reason": r.reason,
        "is_current": r.is_current,
        "faq_count": len(r.snapshot.faqs),
        "doc_count": len(r.snapshot.docs),
        "knowledge_hash": render_knowledge(r.snapshot).hash,
        "created_by": str(r.created_by) if r.created_by else None,
        "eval_run_id": str(r.eval_run_id) if r.eval_run_id else None,
        "created_at": r.created_at.isoformat(),
    }


@router.get("/knowledge/releases")
async def list_knowledge_releases(request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "knowledge.read")
    async with request.app.state.session_factory() as s:
        items = await releases.list_releases(s, tenant_id)
    return {"items": [_release_json(r) for r in items]}


@router.post("/knowledge/releases/{number}/restore", dependencies=[Csrf])
async def restore_knowledge_release(number: int, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "knowledge.manage")
    async with request.app.state.session_factory() as s, s.begin():
        restored = await releases.restore_release(s, tenant_id, number, restored_by=actor.user_id)
    if restored is None:
        raise ApiError(404, "not_found", "Release not found")
    return _release_json(restored)


# ---------- độ phủ + chỗ thiếu (§13.3) ----------


@router.get("/knowledge/coverage")
async def coverage(request: Request, actor: CurrentStaff) -> dict:
    """Mỗi chủ đề: đã có FAQ/tài liệu đã duyệt nào nói tới chưa."""
    tenant_id = _require(actor, "knowledge.read")
    sources: dict[str, list[dict]] = {t: [] for t in TOPICS}
    async with request.app.state.session_factory() as s:
        for kind in ("faq", "doc"):
            for e in await content.list_entries(s, kind, tenant_id):
                if e.status != "approved":
                    continue
                for t in effective_topics(e.topics, f"{e.first} {e.second}"):
                    sources[t].append({"type": kind, "id": str(e.id), "title": e.first})
    return {
        "items": [
            {"topic": t, "label": label, "covered": bool(sources[t]), "sources": sources[t]}
            for t, label in TOPICS.items()
        ]
    }


@router.get("/knowledge/gaps")
async def gaps(
    request: Request, actor: CurrentStaff, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> dict:
    """Bot phải nhờ nhân viên vì thiếu nguồn: đếm theo chủ đề trong `days` ngày gần nhất."""
    tenant_id = _require(actor, "knowledge.read")
    async with request.app.state.session_factory() as s:
        rows = await content.list_knowledge_gaps(s, tenant_id, days)
        items = [
            {"topic": topic, "label": TOPICS.get(topic, "khác"), "count": n} for topic, n in rows
        ]
    return {"days": days, "items": items}


# ---------- catalog ----------


@router.post("/catalog/import", dependencies=[Csrf])
async def import_catalog_csv(request: Request, actor: CurrentStaff) -> dict:
    """Body là CSV thô (`text/csv`), tối đa 2 MB. Một lỗi bất kỳ ⇒ không ghi gì."""
    tenant_id = _require(actor, "knowledge.manage")
    if not request.headers.get("content-type", "").lower().startswith("text/csv"):
        raise ApiError(415, "unsupported_media_type", "text/csv required")
    length = request.headers.get("content-length")
    if length is None or not length.isdigit():
        raise ApiError(411, "length_required", "Content-Length required")
    if int(length) > MAX_CSV_BYTES:
        raise ApiError(413, "payload_too_large", "CSV larger than 2 MB")
    raw = await request.body()
    if len(raw) > MAX_CSV_BYTES:
        raise ApiError(413, "payload_too_large", "CSV larger than 2 MB")
    started = datetime.now(UTC)
    factory = request.app.state.session_factory
    try:
        items = parse_catalog_csv(raw.decode("utf-8"))
        async with factory() as s, s.begin():
            created, updated = await import_catalog(s, tenant_id, items)
            await record_sync_run(
                s, tenant_id, "csv", started, SyncResult(created=created, updated=updated)
            )
    except UnicodeDecodeError as exc:
        raise ApiError(422, "invalid_csv", "CSV must be UTF-8") from exc
    except CatalogCsvError as exc:
        async with factory() as s, s.begin():
            failed = SyncResult(errors=exc.errors)
            await record_sync_run(s, tenant_id, "csv", started, failed, failed=True)
        raise ApiError(422, "invalid_csv", "CSV has errors", details=exc.errors) from exc
    return {"items": len(items), "created": created, "updated": updated}


# ---------- nguồn catalog (§13.2) ----------


class SheetsSourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spreadsheet_id: str = Field(pattern=SPREADSHEET_ID.pattern)
    range: str = Field(default="A:G", pattern=CELL_RANGE.pattern)


def _run_json(run: dict) -> dict:
    return run | {k: run[k].isoformat() for k in ("started_at", "finished_at")}


@router.get("/catalog/sources")
async def list_sources(request: Request, actor: CurrentStaff) -> dict:
    """Cấu hình Sheets + các lần sync gần nhất ("đồng bộ lần cuối lúc…")."""
    tenant_id = _require(actor, "knowledge.read")
    async with request.app.state.session_factory() as s:
        sheets = await get_source_config(s, tenant_id, "sheets")
        runs = await recent_runs(s, tenant_id)
    return {
        # Chủ shop chia sẻ sheet (quyền xem) cho email này.
        "service_account_email": service_account_email(
            request.app.state.settings.google_service_account_json
        ),
        "sheets": sheets,
        "runs": [_run_json(r) for r in runs],
    }


@router.put("/catalog/sources/sheets", dependencies=[Csrf])
async def put_sheets_source(body: SheetsSourceIn, request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "knowledge.manage")
    async with request.app.state.session_factory() as s, s.begin():
        await save_source_config(s, tenant_id, "sheets", body.model_dump())
    return body.model_dump()


@router.post("/catalog/sources/sheets/sync", dependencies=[Csrf])
async def sync_sheets_now(request: Request, actor: CurrentStaff) -> dict:
    tenant_id = _require(actor, "knowledge.manage")
    async with httpx.AsyncClient() as http:
        status, result = await sync_sheets(
            request.app.state.session_factory,
            tenant_id,
            request.app.state.settings.google_service_account_json,
            http,
        )
    return {
        "status": status,
        "created": result.created,
        "updated": result.updated,
        "deactivated": result.deactivated,
        "errors": result.errors[:20],
    }


@router.get("/catalog/search")
async def search(
    request: Request,
    actor: CurrentStaff,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> dict:
    """Thử tìm như tool `search_products` của agent."""
    tenant_id = _require(actor, "knowledge.read")
    async with request.app.state.session_factory() as s:
        items = await search_catalog(s, tenant_id, q, limit)
    return {
        "items": [
            {
                "id": str(i.id),
                "sku": i.sku,
                "name": i.name,
                "price_vnd": i.price_vnd,
                "variants": i.variants,
            }
            for i in items
        ]
    }


@router.get("/catalog/items/{item_id}")
async def get_item(item_id: UUID, request: Request, actor: CurrentStaff) -> dict:
    """Mở bản ghi sản phẩm mà một bản nháp trích làm nguồn (trace view)."""
    tenant_id = _require(actor, "knowledge.read")
    async with request.app.state.session_factory() as s:
        item = await get_catalog_item(s, tenant_id, item_id)
    if item is None:
        raise ApiError(404, "not_found", "Not found")
    return {
        "id": str(item.id),
        "sku": item.sku,
        "name": item.name,
        "description": item.description,
        "price_vnd": item.price_vnd,
        "variants": item.variants,
    }
