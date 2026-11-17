import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.auth import CurrentTenant
from app.errors import ApiError
from app.fetching import RemoteImages
from app.files import Files, storage_missing, timestamp
from app.formats import dimensions, filename_token
from app.images import data_uri_size
from app.rendering import capture, resolve_render
from app.request_id import request_id_var
from app.schemas import RenderRequest, StoredFileResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["render"])

NO_STORE = {"Cache-Control": "no-store"}


@router.post(
    "/render",
    response_class=Response,
    responses={
        200: {
            "content": {"image/png": {}, "application/json": {}},
            "description": "The card, or with output 'url' where it is stored",
        }
    },
)
async def render(request: Request, payload: RenderRequest, tenant: CurrentTenant) -> Response:
    registry = tenant.registry
    pool = request.app.state.browser
    files: Files | None = request.app.state.files
    remote: RemoteImages = request.app.state.remote

    if payload.output == "url" and files is None:
        raise storage_missing("output 'url'")
    uploads = None if files is None else files.uploads_of(tenant.name)
    card = await resolve_render(registry, payload, uploads, remote.fetch)

    # After validation on purpose: a malformed request deserves its 400 even while the
    # browser is down.
    if pool is None or not pool.is_ready:
        raise ApiError(503, "Renderer is not available.")

    html = registry.render_html(
        card.spec, card.format, card.fields, image=card.image, icon=card.icon
    )

    logger.info(
        "Rendering tenant=%s template=%s format=%s output=%s headline_length=%d image_bytes=%d "
        "icon_bytes=%d",
        tenant.name,
        card.spec.id,
        card.format.value,
        payload.output or "binary",
        len(card.fields.get("headline", "")),
        data_uri_size(card.image),
        data_uri_size(card.icon),
    )

    png = await capture(pool.render_png, html, card.format, registry.font_checks(card.spec))

    request_id = request_id_var.get()
    stem = f"{card.spec.bundle}-{card.spec.name}"
    filename = f"{stem}_{filename_token(card.format)}_{request_id[:8]}.png"
    if payload.output == "url":
        saved = await files.save_output(tenant.name, filename, png, "image/png")
        width, height = dimensions(card.format)
        body = StoredFileResponse(
            url=saved.url,
            content_type="image/png",
            width=width,
            height=height,
            bytes=saved.size,
            expires_at=timestamp(saved.expires_at),
        )
        return JSONResponse(body.model_dump(exclude_none=True), headers=NO_STORE)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{filename}"'} | NO_STORE,
    )
