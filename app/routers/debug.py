import struct
import zlib
from functools import lru_cache
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response
from jinja2 import Environment
from pydantic import ValidationError

from app.formats import Format
from app.rendering import (
    reject_image_and_icon,
    resolve_fields,
    resolve_format,
    resolve_image,
    resolve_template,
)
from app.schemas import RESERVED_KEYS, RenderRequest
from app.templating import TemplateSpec

router = APIRouter(prefix="/v1/debug", tags=["debug"], include_in_schema=False)

ROUTE_PARAMETERS = frozenset({"template", "format", "image_url", "icon_url", "fit"})
NO_STORE = {"Cache-Control": "no-store"}

SAMPLE_IMAGE_SIZE = (1200, 675)
SAMPLE_IMAGE_COLOURS = ((70, 110, 160), (220, 228, 236))
SAMPLE_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#000" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="12" cy="12" r="10"/><path d="m7.5 12.5 3 3 6-7"/></svg>'
)

INDEX = Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True).from_string(
    """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>contentgun-assets templates</title>
    <style>
      body {
        margin: 40px;
        font: 16px/1.5 system-ui, sans-serif;
        color: #222222;
        background: #f7f7f5;
      }
      h1 { font-size: 24px; }
      h2 { margin: 48px 0 0; font-size: 20px; }
      h3 { margin: 32px 0 0; font-size: 17px; }
      p { margin: 4px 0 12px; max-width: 72ch; }
      table { border-collapse: collapse; }
      th, td { padding: 4px 32px 4px 0; text-align: left; }
      a { color: #0b5cad; }
    </style>
  </head>
  <body>
    <h1>Templates</h1>
    <p>Every link opens the debug route with the template's sample content. The image and icon
    variants use <a href="{{ samples.image_url }}">a sample image</a> and
    <a href="{{ samples.icon_url }}">a sample icon</a>.</p>
    {% for bundle in bundles %}
    <h2>Bundle {{ bundle.name }}</h2>
    <p>{{ bundle.description }}</p>
    {% for template in bundle.templates %}
    <h3>{{ template.id }}</h3>
    <p>{{ template.description }}</p>
    <table>
      <tr><th>Variant</th>{% for fmt in template.formats %}<th>{{ fmt }}</th>{% endfor %}</tr>
      {% for variant in template.variants %}
      <tr>
        <td>{{ variant.label }}</td>
        {% for link in variant.links %}
        <td><a href="{{ link.href }}">{{ link.format }}</a></td>
        {% endfor %}
      </tr>
      {% endfor %}
    </table>
    {% endfor %}
    {% endfor %}
  </body>
</html>
"""
)


def _variants(spec: TemplateSpec, samples: dict[str, str]) -> list[tuple[str, dict[str, str]]]:
    variants: list[tuple[str, dict[str, str]]] = [("Sample", {})]
    optional = {field: "" for field in spec.samples if field not in spec.required}
    if optional:
        variants.append(("Required fields only", optional))
    if spec.accepts_image and spec.fallback_image is None:
        variants.append(("With an image", {"image_url": samples["image_url"]}))
    if spec.accepts_icon:
        icon = {"icon_url": samples["icon_url"]}
        variants.append(("With an icon", icon))
        if "icon_color" in spec.fields:
            variants.append(("With an icon in the text color", icon | {"icon_color": "text"}))
    return variants


def _href(page: str, spec: TemplateSpec, fmt: Format, parameters: dict[str, str]) -> str:
    query = {"template": spec.id, "format": fmt.value} | parameters
    return f"{page}?{urlencode(query, safe=':/')}"


@router.get("", response_class=HTMLResponse)
async def debug_index(request: Request) -> HTMLResponse:
    registry = request.app.state.templates
    page = request.app.url_path_for("debug_html")
    samples = {
        "image_url": request.app.url_path_for("sample_image"),
        "icon_url": request.app.url_path_for("sample_icon"),
    }
    bundles = []
    for bundle in registry.bundles:
        templates = []
        for spec in bundle.specs.values():
            variants = [
                {
                    "label": label,
                    "links": [
                        {"format": fmt.value, "href": _href(page, spec, fmt, parameters)}
                        for fmt in spec.formats
                    ],
                }
                for label, parameters in _variants(spec, samples)
            ]
            templates.append(
                {
                    "id": spec.id,
                    "description": spec.description,
                    "formats": [fmt.value for fmt in spec.formats],
                    "variants": variants,
                }
            )
        bundles.append(
            {"name": bundle.name, "description": bundle.description, "templates": templates}
        )
    return HTMLResponse(INDEX.render(bundles=bundles, samples=samples), headers=NO_STORE)


@router.get("/samples/icon.svg")
async def sample_icon() -> Response:
    return Response(SAMPLE_ICON, media_type="image/svg+xml", headers=NO_STORE)


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


@lru_cache(maxsize=1)
def sample_png() -> bytes:
    """A vertical gradient, drawn here so the debug routes need no image file of a bundle."""
    width, height = SAMPLE_IMAGE_SIZE
    top, bottom = SAMPLE_IMAGE_COLOURS
    rows = bytearray()
    for y in range(height):
        share = y / (height - 1)
        pixel = bytes(round(a + (b - a) * share) for a, b in zip(top, bottom, strict=True))
        rows += b"\x00" + pixel * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + _png_chunk(b"IEND", b"")
    )


@router.get("/samples/image.png")
async def sample_image() -> Response:
    return Response(sample_png(), media_type="image/png", headers=NO_STORE)


@router.get("/html", response_class=HTMLResponse)
async def debug_html(
    request: Request,
    template: str | None = None,
    format: Format | None = None,
    image_url: str | None = None,
    icon_url: str | None = None,
    fit: int = 1,
) -> HTMLResponse:
    registry = request.app.state.templates

    spec = resolve_template(registry, template)
    fmt = resolve_format(spec, format)

    # Through the request model, so the typed limits of the render endpoint apply here too.
    overrides = {
        key: value for key, value in request.query_params.items() if key not in ROUTE_PARAMETERS
    }
    try:
        payload = RenderRequest.model_validate(spec.samples | overrides)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None
    fields = resolve_fields(
        spec,
        {
            key: value
            for key, value in payload.model_dump(exclude_unset=True).items()
            if key not in RESERVED_KEYS
        },
    )

    # A base64 image or icon cannot be typed into a URL, so the design loop points at one
    # instead. Only the designer's own browser fetches it; the service never does. Without an
    # image the page falls back exactly like the screenshot does.
    reject_image_and_icon(image_url, icon_url)
    image = image_url if image_url and spec.accepts_image else resolve_image(spec, None)
    icon = icon_url if icon_url and spec.accepts_icon else None

    html = registry.render_html(spec, fmt, fields, fit_text=bool(fit), image=image, icon=icon)
    return HTMLResponse(html, headers=NO_STORE)
