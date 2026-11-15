import logging

from fastapi import APIRouter, Depends, Request, Response

from app.auth import verify_api_key
from app.errors import ApiError
from app.formats import filename_token
from app.images import data_uri_size
from app.rendering import capture, resolve_render
from app.request_id import request_id_var
from app.schemas import RenderRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["render"], dependencies=[Depends(verify_api_key)])


@router.post(
    "/render",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}, "description": "Rendered card"}},
)
async def render(request: Request, payload: RenderRequest) -> Response:
    registry = request.app.state.templates
    pool = request.app.state.browser

    card = resolve_render(registry, payload)

    # After validation on purpose: a malformed request deserves its 400 even while the
    # browser is down.
    if pool is None or not pool.is_ready:
        raise ApiError(503, "Renderer is not available.")

    html = registry.render_html(
        card.spec, card.format, card.fields, image=card.image, icon=card.icon
    )

    logger.info(
        "Rendering template=%s format=%s headline_length=%d image_bytes=%d icon_bytes=%d",
        card.spec.id,
        card.format.value,
        len(card.fields.get("headline", "")),
        data_uri_size(card.image),
        data_uri_size(card.icon),
    )

    png = await capture(pool.render_png, html, card.format, registry.font_checks(card.spec))

    request_id = request_id_var.get()
    stem = f"{card.spec.bundle}-{card.spec.name}"
    filename = f"{stem}_{filename_token(card.format)}_{request_id[:8]}.png"
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
