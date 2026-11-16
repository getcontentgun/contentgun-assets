import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from app.browser import FontNotLoaded, RenderBusy, RenderTimeout, RenderUnavailable
from app.errors import ApiError
from app.files import Kind, TenantUploads, storage_missing
from app.formats import Format, dimensions
from app.images import decode_icon, decode_image, file_data_uri, icon_data_uri, image_data_uri
from app.schemas import MAX_FIELD_LENGTH, RESERVED_KEYS, RenderRequest
from app.templating import TemplateRegistry, TemplateSpec, UnknownTemplateError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedRender:
    spec: TemplateSpec
    format: Format
    fields: dict[str, str]
    image: str | None
    icon: str | None


def resolve_template(registry: TemplateRegistry, template_id: str | None) -> TemplateSpec:
    wanted = registry.default_template if template_id is None else template_id
    if wanted is None:
        raise ApiError(400, f"Name a template. Available: {', '.join(registry.ids)}.")
    try:
        return registry.get(wanted)
    except UnknownTemplateError:
        raise ApiError(
            400,
            f"Unknown template {wanted!r}. Available: {', '.join(registry.ids)}.",
        ) from None


def resolve_format(spec: TemplateSpec, fmt: Format | None) -> Format:
    wanted = spec.formats[0] if fmt is None else fmt
    if not spec.supports(wanted):
        supported = ", ".join(f.value for f in spec.formats)
        raise ApiError(
            400,
            f"Format {wanted.value!r} is not supported by template {spec.id!r}. "
            f"Supported: {supported}.",
        )
    return wanted


def _given(raw: str | None) -> bool:
    return raw is not None and bool(raw.strip())


def _either(inline: str | None, upload: str | None) -> str | None:
    return inline if _given(inline) else upload


def reject_image_and_icon(image: str | None, icon: str | None) -> None:
    if _given(image) and _given(icon):
        raise ApiError(400, "Send either an image or an icon, not both.")


def reject_two_sources(inline: str | None, upload: str | None, kind: Kind) -> None:
    if _given(inline) and _given(upload):
        raise ApiError(400, f"Send the {kind} either as {kind} or as {kind}_upload, not both.")


def _uploads(uploads: TenantUploads | None, kind: Kind) -> TenantUploads:
    if uploads is None:
        raise storage_missing(f"{kind}_upload")
    return uploads


async def resolve_upload(
    spec: TemplateSpec, upload_id: str, kind: Kind, uploads: TenantUploads | None
) -> str:
    """An upload of the tenant as the data URI of the template's image or icon."""
    accepts = spec.accepts_image if kind == "image" else spec.accepts_icon
    if not accepts:
        raise ApiError(400, f"Template {spec.id!r} does not accept an {kind}.")
    data = await _uploads(uploads, kind).read(upload_id.strip(), kind)
    return image_data_uri(data) if kind == "image" else icon_data_uri(data)


async def upload_size(payload: RenderRequest, uploads: TenantUploads | None) -> int:
    """The bytes of the uploads a request names, read from the bucket's metadata."""
    size = 0
    for upload_id, kind in ((payload.image_upload, "image"), (payload.icon_upload, "icon")):
        if _given(upload_id):
            size += (await _uploads(uploads, kind).find(upload_id.strip(), kind)).size
    return size


def resolve_image(spec: TemplateSpec, raw: str | None) -> str | None:
    if not _given(raw):
        if spec.fallback_image is None:
            return None
        return file_data_uri(spec.fallback_image)
    if not spec.accepts_image:
        raise ApiError(400, f"Template {spec.id!r} does not accept an image.")
    return decode_image(raw)


def resolve_icon(spec: TemplateSpec, raw: str | None) -> str | None:
    if not _given(raw):
        return None
    if not spec.accepts_icon:
        raise ApiError(400, f"Template {spec.id!r} does not accept an icon.")
    return decode_icon(raw)


def resolve_fields(spec: TemplateSpec, requested: dict[str, object]) -> dict[str, str]:
    unknown = sorted(set(requested) - set(spec.fields) - RESERVED_KEYS)
    if unknown:
        raise ApiError(
            400,
            f"Field {unknown[0]!r} is not accepted by template {spec.id!r}. "
            f"Accepted: {', '.join(spec.fields)}.",
        )

    fields: dict[str, str] = {}
    for name in spec.fields:
        value = requested.get(name)
        if value is None:
            value = spec.defaults.get(name)
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ApiError(400, f"Field {name!r} must be a string.")
        limit = spec.limits.get(name, MAX_FIELD_LENGTH)
        if len(value) > limit:
            raise ApiError(400, f"Field {name!r} must be at most {limit} characters.")
        fields[name] = value

    missing = [name for name in spec.required if not fields.get(name, "").strip()]
    if missing:
        raise ApiError(400, f"Field {missing[0]!r} is required by template {spec.id!r}.")

    return fields


async def resolve_render(
    registry: TemplateRegistry, payload: RenderRequest, uploads: TenantUploads | None = None
) -> ResolvedRender:
    spec = resolve_template(registry, payload.template)
    fmt = resolve_format(spec, payload.format)
    # exclude_unset, not a plain dump: the typed optional fields would otherwise arrive as None
    # and be rejected by every template that does not declare them.
    requested = {
        key: value
        for key, value in payload.model_dump(exclude_unset=True).items()
        if key not in RESERVED_KEYS
    }
    fields = resolve_fields(spec, requested)
    reject_two_sources(payload.image, payload.image_upload, "image")
    reject_two_sources(payload.icon, payload.icon_upload, "icon")
    reject_image_and_icon(
        _either(payload.image, payload.image_upload), _either(payload.icon, payload.icon_upload)
    )
    if _given(payload.image_upload):
        image = await resolve_upload(spec, payload.image_upload, "image", uploads)
    else:
        image = resolve_image(spec, payload.image)
    if _given(payload.icon_upload):
        icon = await resolve_upload(spec, payload.icon_upload, "icon", uploads)
    else:
        icon = resolve_icon(spec, payload.icon)
    return ResolvedRender(spec=spec, format=fmt, fields=fields, image=image, icon=icon)


async def capture(
    render: Callable[[str, int, int, Sequence[str]], Awaitable[bytes]],
    html: str,
    fmt: Format,
    font_checks: Sequence[str],
) -> bytes:
    width, height = dimensions(fmt)
    try:
        return await render(html, width, height, font_checks)
    except RenderBusy:
        raise ApiError(503, "Renderer is busy, retry later.") from None
    except RenderUnavailable:
        raise ApiError(503, "Renderer is shutting down.") from None
    except RenderTimeout:
        raise ApiError(504, "Render timed out.") from None
    except FontNotLoaded as exc:
        raise ApiError(500, f"Brand font not loaded: {exc}.") from None
    except Exception as exc:
        logger.error("Render failed: %s: %s", type(exc).__name__, exc)
        raise ApiError(500, "Rendering failed.") from None
