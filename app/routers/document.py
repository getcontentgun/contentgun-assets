import logging

from fastapi import APIRouter, Depends, Request, Response

from app.auth import verify_api_key
from app.config import settings
from app.document import merge_pdfs
from app.errors import ApiError
from app.formats import filename_token
from app.images import data_uri_size, estimated_size, readable_size
from app.rendering import ResolvedRender, capture, resolve_render
from app.request_id import request_id_var
from app.schemas import DocumentRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["document"], dependencies=[Depends(verify_api_key)])


@router.post(
    "/document",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "One page per slide"}},
)
async def document(request: Request, payload: DocumentRequest) -> Response:
    registry = request.app.state.templates
    pool = request.app.state.browser

    # For the whole document and before any slide is decoded: every slide may stay under
    # the per-image limit while all of them together are far above it.
    graphics = sum(
        estimated_size(slide.image) + estimated_size(slide.icon) for slide in payload.slides
    )
    if graphics > settings.max_image_bytes:
        raise ApiError(
            413,
            "The images and icons of a document must be at most "
            f"{readable_size(settings.max_image_bytes)} together.",
        )

    slides: list[ResolvedRender] = []
    for number, slide in enumerate(payload.slides, start=1):
        try:
            slides.append(resolve_render(registry, slide))
        except ApiError as exc:
            raise ApiError(exc.status_code, f"Slide {number}: {exc.message}") from None

    if len({slide.format for slide in slides}) > 1:
        raise ApiError(400, "All slides must use the same format.")
    fmt = slides[0].format

    # After validation on purpose, as on the render endpoint.
    if pool is None or not pool.is_ready:
        raise ApiError(503, "Renderer is not available.")

    logger.info(
        "Rendering document format=%s slides=%d templates=%s image_bytes=%d icon_bytes=%d",
        fmt.value,
        len(slides),
        ",".join(slide.spec.id for slide in slides),
        sum(data_uri_size(slide.image) for slide in slides),
        sum(data_uri_size(slide.icon) for slide in slides),
    )

    pages = []
    for slide in slides:
        html = registry.render_html(
            slide.spec, fmt, slide.fields, image=slide.image, icon=slide.icon
        )
        checks = registry.font_checks(slide.spec)
        pages.append(await capture(pool.render_pdf, html, fmt, checks))
    pdf = merge_pdfs(pages)

    request_id = request_id_var.get()
    filename = f"document_{filename_token(fmt)}_{request_id[:8]}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
