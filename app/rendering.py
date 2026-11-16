import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from app.browser import FontNotLoaded, RenderBusy, RenderTimeout, RenderUnavailable
from app.errors import ApiError
from app.formats import Format, dimensions
from app.images import decode_icon, decode_image, file_data_uri
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


def reject_image_and_icon(image: str | None, icon: str | None) -> None:
    if _given(image) and _given(icon):
        raise ApiError(400, "Send either an image or an icon, not both.")


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


def resolve_render(registry: TemplateRegistry, payload: RenderRequest) -> ResolvedRender:
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
    reject_image_and_icon(payload.image, payload.icon)
    image = resolve_image(spec, payload.image)
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
