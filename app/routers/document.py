import asyncio
import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.auth import CurrentTenant
from app.config import settings
from app.document import merge_pdfs
from app.errors import ApiError
from app.fetching import CONCURRENCY, Budget, OverBudget, RemoteImages
from app.files import Files, Kind, storage_missing, timestamp
from app.formats import dimensions, filename_token
from app.images import data_uri_size, estimated_size, readable_size
from app.rendering import (
    CheckedRender,
    ResolvedRender,
    capture,
    check_render,
    finish_render,
    upload_size,
)
from app.request_id import request_id_var
from app.schemas import DocumentRequest, StoredFileResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["document"])

NO_STORE = {"Cache-Control": "no-store"}


def _numbered(number: int, exc: ApiError) -> ApiError:
    return ApiError(exc.status_code, f"Slide {number}: {exc.message}")


async def _fetch_urls(
    remote: RemoteImages, cards: list[CheckedRender], budget: Budget
) -> dict[tuple[str, Kind], bytes]:
    """The files behind the URLs of all slides, each URL fetched once and a few at a time, yet
    counted on the budget once per slide that uses it, as base64 and uploads are: every slide
    gets a data URI of its own. An error names the first slide with the URL; running out of
    budget stops every fetch."""
    used_on: dict[tuple[str, Kind], list[int]] = {}
    for number, card in enumerate(cards, start=1):
        if card.pending is not None and card.pending.source == "url":
            used_on.setdefault((card.pending.value, card.pending.kind), []).append(number)

    fetched: dict[tuple[str, Kind], bytes] = {}
    slots = asyncio.Semaphore(CONCURRENCY)

    async def fetch(url: str, kind: Kind, numbers: list[int]) -> None:
        async with slots:
            try:
                fetched[(url, kind)] = await remote.fetch(url, kind, budget, len(numbers))
            except OverBudget:
                raise
            except ApiError as exc:
                raise _numbered(numbers[0], exc) from None

    try:
        async with asyncio.TaskGroup() as group:
            for (url, kind), numbers in used_on.items():
                group.create_task(fetch(url, kind, numbers))
    except ExceptionGroup as failed:
        raise failed.exceptions[0] from None
    return fetched


@router.post(
    "/document",
    response_class=Response,
    responses={
        200: {
            "content": {"application/pdf": {}, "application/json": {}},
            "description": "One page per slide, or with output 'url' where the PDF is stored",
        }
    },
)
async def document(request: Request, payload: DocumentRequest, tenant: CurrentTenant) -> Response:
    registry = tenant.registry
    pool = request.app.state.browser
    files: Files | None = request.app.state.files
    remote: RemoteImages = request.app.state.remote

    if payload.output == "url" and files is None:
        raise storage_missing("output 'url'")
    for number, slide in enumerate(payload.slides, start=1):
        if "output" in slide.model_fields_set:
            raise ApiError(400, f"Slide {number}: output belongs to the document, not to a slide.")

    # For the whole document and before any slide is decoded: every slide may stay under
    # the per-image limit while all of them together are far above it.
    over_budget = ApiError(
        413,
        "The images and icons of a document must be at most "
        f"{readable_size(settings.max_image_bytes)} together.",
    )
    graphics = sum(
        estimated_size(slide.image) + estimated_size(slide.icon) for slide in payload.slides
    )
    if graphics > settings.max_image_bytes:
        raise over_budget
    uploads = None if files is None else files.uploads_of(tenant.name)
    for number, slide in enumerate(payload.slides, start=1):
        try:
            graphics += await upload_size(slide, uploads)
        except ApiError as exc:
            raise _numbered(number, exc) from None
        if graphics > settings.max_image_bytes:
            raise over_budget

    cards: list[CheckedRender] = []
    for number, slide in enumerate(payload.slides, start=1):
        try:
            cards.append(check_render(registry, slide))
        except ApiError as exc:
            raise _numbered(number, exc) from None

    if len({card.format for card in cards}) > 1:
        raise ApiError(400, "All slides must use the same format.")
    fmt = cards[0].format

    budget = Budget(settings.max_image_bytes - graphics, over_budget.message)
    fetched = await _fetch_urls(remote, cards, budget)

    async def from_fetched(url: str, kind: Kind) -> bytes:
        return fetched[(url, kind)]

    slides: list[ResolvedRender] = []
    for number, card in enumerate(cards, start=1):
        try:
            slides.append(await finish_render(card, uploads, from_fetched))
        except ApiError as exc:
            raise _numbered(number, exc) from None

    # After validation on purpose, as on the render endpoint.
    if pool is None or not pool.is_ready:
        raise ApiError(503, "Renderer is not available.")

    logger.info(
        "Rendering document tenant=%s format=%s output=%s slides=%d templates=%s image_bytes=%d "
        "icon_bytes=%d",
        tenant.name,
        fmt.value,
        payload.output or "binary",
        len(slides),
        ",".join(slide.spec.id for slide in slides),
        sum(data_uri_size(slide.image) for slide in slides),
        sum(data_uri_size(slide.icon) for slide in slides),
    )

    pages = []
    for slide in slides:
        html = registry.render_html(
            slide.spec, fmt, slide.fields, image=slide.image, icon=slide.icon, code=slide.code
        )
        checks = registry.font_checks(slide.spec)
        pages.append(await capture(pool.render_pdf, html, fmt, checks))
    pdf = merge_pdfs(pages)

    request_id = request_id_var.get()
    filename = f"document_{filename_token(fmt)}_{request_id[:8]}.pdf"
    if payload.output == "url":
        saved = await files.save_output(tenant.name, filename, pdf, "application/pdf")
        width, height = dimensions(fmt)
        body = StoredFileResponse(
            url=saved.url,
            content_type="application/pdf",
            width=width,
            height=height,
            bytes=saved.size,
            expires_at=timestamp(saved.expires_at),
            pages=len(pages),
        )
        return JSONResponse(body.model_dump(exclude_none=True), headers=NO_STORE)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'} | NO_STORE,
    )
