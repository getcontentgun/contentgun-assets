import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.browser import FontNotLoaded, RenderBusy, RenderTimeout, RenderUnavailable
from app.code import Code, CodeError, check_code
from app.errors import ApiError
from app.fetching import checked_url, url_subject
from app.files import Kind, TenantUploads, storage_missing
from app.formats import Format, dimensions
from app.images import decode_icon, decode_image, file_data_uri, icon_data_uri, image_data_uri
from app.schemas import MAX_FIELD_LENGTH, RESERVED_KEYS, RenderRequest
from app.templating import TemplateRegistry, TemplateSpec, UnknownTemplateError

logger = logging.getLogger(__name__)

Fetch = Callable[[str, Kind], Awaitable[bytes]]


@dataclass(frozen=True)
class ResolvedRender:
    spec: TemplateSpec
    format: Format
    fields: dict[str, str]
    image: str | None
    icon: str | None
    code: Code | None = None


@dataclass(frozen=True)
class Pending:
    """The image or the icon of a request whose bytes are still to be read: an upload or a URL."""

    kind: Kind
    source: Literal["upload", "url"]
    value: str


@dataclass(frozen=True)
class CheckedRender:
    """A request that passed every check that needs neither the bucket nor the network."""

    spec: TemplateSpec
    format: Format
    fields: dict[str, str]
    image: str | None
    icon: str | None
    pending: Pending | None
    code: Code | None = None


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


def _sources(kind: Kind) -> tuple[str, str, str]:
    return kind, f"{kind}_upload", f"{kind}_url"


def _source(payload: RenderRequest, kind: Kind) -> str | None:
    for name in _sources(kind):
        value = getattr(payload, name)
        if _given(value):
            return value
    return None


def reject_image_and_icon(image: str | None, icon: str | None) -> None:
    if _given(image) and _given(icon):
        raise ApiError(400, "Send either an image or an icon, not both.")


def reject_two_sources(payload: RenderRequest, kind: Kind) -> None:
    if sum(_given(getattr(payload, name)) for name in _sources(kind)) > 1:
        inline, upload, url = _sources(kind)
        raise ApiError(400, f"Send the {kind} in one field only: {inline}, {upload} or {url}.")


def _accept(spec: TemplateSpec, kind: Kind) -> None:
    accepts = spec.accepts_image if kind == "image" else spec.accepts_icon
    if not accepts:
        raise ApiError(400, f"Template {spec.id!r} does not accept an {kind}.")


def _pending(payload: RenderRequest, kind: Kind) -> Pending | None:
    _, upload, url = _sources(kind)
    if _given(getattr(payload, upload)):
        return Pending(kind, "upload", getattr(payload, upload).strip())
    if _given(getattr(payload, url)):
        return Pending(kind, "url", getattr(payload, url).strip())
    return None


def _uploads(uploads: TenantUploads | None, kind: Kind) -> TenantUploads:
    if uploads is None:
        raise storage_missing(f"{kind}_upload")
    return uploads


async def resolve_upload(upload_id: str, kind: Kind, uploads: TenantUploads | None) -> str:
    """An upload of the tenant as the data URI of the template's image or icon."""
    data = await _uploads(uploads, kind).read(upload_id, kind)
    return image_data_uri(data) if kind == "image" else icon_data_uri(data)


async def resolve_url(url: str, kind: Kind, fetch: Fetch) -> str:
    """The file behind a URL as the data URI of the template's image or icon, checked like an
    image or icon in the body, whatever type the other side claims for it."""
    data = await fetch(url, kind)
    if kind == "image":
        return image_data_uri(data, url_subject(kind), "icon_url")
    return icon_data_uri(data, url_subject(kind), "image_url")


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


def resolve_code(
    spec: TemplateSpec,
    code: str | None,
    language: str | None = None,
    line_numbers: bool | None = None,
    highlight_lines: Sequence[int] | None = None,
) -> Code | None:
    """The code of a request, checked and highlighted for a template with code = true. A blank
    code or language, false line numbers and no highlighted lines count as not sent."""
    sent = _given(code) or _given(language) or bool(line_numbers) or bool(highlight_lines)
    if not spec.accepts_code:
        if sent:
            raise ApiError(400, f"Template {spec.id!r} does not accept code.")
        return None
    if not _given(code):
        raise ApiError(400, f"Field 'code' is required by template {spec.id!r}.")
    try:
        return check_code(code, language, bool(line_numbers), highlight_lines or ())
    except CodeError as exc:
        raise ApiError(400, str(exc)) from None


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


def check_render(registry: TemplateRegistry, payload: RenderRequest) -> CheckedRender:
    """Everything of a request that can be checked before an upload is read or a URL fetched,
    so a request that cannot render never costs a fetch."""
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
    code = resolve_code(
        spec, payload.code, payload.language, payload.line_numbers, payload.highlight_lines
    )
    reject_two_sources(payload, "image")
    reject_two_sources(payload, "icon")
    reject_image_and_icon(_source(payload, "image"), _source(payload, "icon"))
    pending = _pending(payload, "image") or _pending(payload, "icon")
    if pending is not None:
        _accept(spec, pending.kind)
        if pending.source == "url":
            checked_url(pending.value, pending.kind)
    pending_kind = None if pending is None else pending.kind
    image = None if pending_kind == "image" else resolve_image(spec, payload.image)
    icon = None if pending_kind == "icon" else resolve_icon(spec, payload.icon)
    return CheckedRender(
        spec=spec, format=fmt, fields=fields, image=image, icon=icon, pending=pending, code=code
    )


async def finish_render(
    card: CheckedRender, uploads: TenantUploads | None, fetch: Fetch
) -> ResolvedRender:
    image, icon = card.image, card.icon
    pending = card.pending
    if pending is not None:
        if pending.source == "upload":
            uri = await resolve_upload(pending.value, pending.kind, uploads)
        else:
            uri = await resolve_url(pending.value, pending.kind, fetch)
        if pending.kind == "image":
            image = uri
        else:
            icon = uri
    return ResolvedRender(
        spec=card.spec,
        format=card.format,
        fields=card.fields,
        image=image,
        icon=icon,
        code=card.code,
    )


async def resolve_render(
    registry: TemplateRegistry,
    payload: RenderRequest,
    uploads: TenantUploads | None,
    fetch: Fetch,
) -> ResolvedRender:
    return await finish_render(check_render(registry, payload), uploads, fetch)


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
