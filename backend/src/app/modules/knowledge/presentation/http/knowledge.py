"""REST knowledge (thiết kế bước 2 §8.1): FAQ, tài liệu nhỏ, import + thử tìm catalog."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.modules.identity.domain.authorization import StaffActor, authorize
from app.modules.identity.presentation.http.auth import Csrf, CurrentStaff
from app.modules.knowledge.domain.catalog_csv import CatalogCsvError, parse_catalog_csv
from app.modules.knowledge.infrastructure import content
from app.modules.knowledge.infrastructure.catalog import (
    get_catalog_item,
    import_catalog,
    search_catalog,
)
from app.shared.presentation.http_errors import ApiError

MAX_CSV_BYTES = 2 * 1024 * 1024

router = APIRouter(prefix="/api/v1")


def _require(actor: StaffActor, action: str) -> UUID:
    if not authorize(actor, action, actor.tenant_id):
        raise ApiError(403, "forbidden", "Not allowed")
    return actor.tenant_id


# ---------- FAQ + tài liệu: cùng hình dạng, khác tên cột ----------


class FaqIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=4000)
    status: Literal["draft", "approved"] = "approved"


class FaqPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str | None = Field(default=None, min_length=1, max_length=500)
    answer: str | None = Field(default=None, min_length=1, max_length=4000)
    status: Literal["draft", "approved", "archived"] | None = None


class DocIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    status: Literal["draft", "approved"] = "approved"


class DocPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
                s, kind, tenant_id, getattr(body, a), getattr(body, b), body.status
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
        if entry is None:
            raise ApiError(404, "not_found", "Not found")
        return Response(status_code=204)


_register("faq", "/knowledge/faqs", ("question", "answer"), FaqIn, FaqPatch)
_register("doc", "/knowledge/docs", ("title", "body"), DocIn, DocPatch)


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
    try:
        items = parse_catalog_csv(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ApiError(422, "invalid_csv", "CSV must be UTF-8") from exc
    except CatalogCsvError as exc:
        raise ApiError(422, "invalid_csv", "CSV has errors", details=exc.errors) from exc
    async with request.app.state.session_factory() as s, s.begin():
        created, updated = await import_catalog(s, tenant_id, items)
    return {"items": len(items), "created": created, "updated": updated}


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
