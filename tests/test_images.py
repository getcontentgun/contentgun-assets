import base64
import pyexpat
import textwrap

import pytest

from app.config import settings
from app.errors import ApiError
from app.images import (
    MIN_EXPAT,
    data_uri_size,
    decode_icon,
    decode_image,
    estimated_size,
    file_data_uri,
)
from app.templating import TemplateRegistry
from tests.conftest import BUILTIN_DIR, CARD_IMAGE, SQUARE_SVG, image_base64, svg_base64


def test_plain_base64_becomes_a_png_data_uri():
    assert decode_image(image_base64()).startswith("data:image/png;base64,")


def test_a_data_uri_is_accepted_and_normalised():
    raw = image_base64()
    assert decode_image(f"data:image/png;base64,{raw}") == decode_image(raw)


def test_line_wrapped_base64_is_tolerated():
    raw = image_base64((32, 32))
    assert decode_image("\n".join(textwrap.wrap(raw, 60))) == decode_image(raw)


def test_jpeg_is_detected():
    assert decode_image(image_base64(fmt="JPEG")).startswith("data:image/jpeg;base64,")


def test_webp_is_detected():
    assert decode_image(image_base64(fmt="WEBP")).startswith("data:image/webp;base64,")


def test_broken_base64_raises_400():
    with pytest.raises(ApiError) as excinfo:
        decode_image("this is not base64 !!")
    assert excinfo.value.status_code == 400
    assert excinfo.value.message == "Field 'image' must be base64-encoded image data."


def test_base64_that_is_not_an_image_raises_400():
    payload = base64.b64encode(b"plain text, correctly encoded").decode("ascii")
    with pytest.raises(ApiError) as excinfo:
        decode_image(payload)
    assert excinfo.value.status_code == 400
    assert excinfo.value.message == "Field 'image' must be a PNG, JPEG or WebP image."


def test_an_svg_sent_as_image_is_pointed_at_the_icon():
    with pytest.raises(ApiError) as excinfo:
        decode_image(svg_base64())
    assert excinfo.value.message == (
        "Field 'image' must be a PNG, JPEG or WebP image. Send an SVG as 'icon'."
    )


def test_a_data_uri_with_a_charset_is_accepted():
    raw = image_base64()
    assert decode_image(f"data:image/png;charset=utf-8;base64,{raw}") == decode_image(raw)


def test_oversized_image_raises_413(monkeypatch):
    monkeypatch.setattr(settings, "max_image_bytes", 1024)
    oversized = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 4096).decode("ascii")
    with pytest.raises(ApiError) as excinfo:
        decode_image(oversized)
    assert excinfo.value.status_code == 413
    assert "at most" in excinfo.value.message


def test_data_uri_size_reports_the_decoded_byte_count():
    raw = image_base64((16, 16))
    assert data_uri_size(decode_image(raw)) == len(base64.b64decode(raw))


def test_data_uri_size_of_nothing_is_zero():
    assert data_uri_size(None) == 0


def test_the_template_fallback_is_a_usable_image():
    spec = TemplateRegistry.load(BUILTIN_DIR).get(CARD_IMAGE)
    assert spec.fallback_image is not None
    assert file_data_uri(spec.fallback_image).startswith("data:image/jpeg;base64,")


def test_estimated_size_ignores_prefix_and_line_breaks():
    raw = image_base64((32, 32))
    wrapped = "data:image/png;base64," + "\n".join(textwrap.wrap(raw, 60))
    assert estimated_size(wrapped) == estimated_size(raw) == len(raw) * 3 // 4
    assert estimated_size(None) == 0


def test_the_running_expat_refuses_entity_expansion():
    assert pyexpat.version_info >= MIN_EXPAT


def icon_error(raw: str) -> ApiError:
    with pytest.raises(ApiError) as excinfo:
        decode_icon(raw)
    return excinfo.value


def svg(body: str = "", root: str = 'viewBox="0 0 16 16"') -> str:
    return svg_base64(f'<svg xmlns="http://www.w3.org/2000/svg" {root}>{body}</svg>')


def test_an_icon_becomes_an_svg_data_uri():
    uri = decode_icon(svg_base64())
    assert uri.startswith("data:image/svg+xml;base64,")
    assert base64.b64decode(uri.partition(",")[2]).decode("utf-8") == SQUARE_SVG


@pytest.mark.parametrize(
    "prefix", ["data:image/svg+xml;base64,", "data:image/svg+xml;charset=utf-8;base64,"]
)
def test_an_icon_data_uri_is_accepted_and_normalised(prefix):
    assert decode_icon(prefix + svg_base64()) == decode_icon(svg_base64())


def test_an_icon_with_declaration_comment_and_bootstrap_attributes_is_accepted():
    markup = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!-- bootstrap-icons -->\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" '
        'class="bi bi-lightbulb" viewBox="0 0 16 16"><path d="M2 6a6 6 0 1 1 10.174 4.31"/></svg>'
    )
    assert decode_icon(svg_base64(markup)).startswith("data:image/svg+xml;base64,")


def test_raw_svg_markup_is_asked_to_be_encoded():
    assert icon_error(SQUARE_SVG).message == (
        "Field 'icon' must be base64-encoded. Encode the SVG markup first."
    )


def test_broken_icon_base64_raises_400():
    error = icon_error("this is not base64 !!")
    assert error.status_code == 400
    assert error.message == "Field 'icon' must be base64-encoded SVG data."


def test_a_raster_image_sent_as_icon_is_pointed_at_the_image():
    assert icon_error(image_base64()).message == (
        "Field 'icon' must be an SVG document. Send a PNG, JPEG or WebP as 'image'."
    )


@pytest.mark.parametrize(
    "markup", ["plain text", "<svg", '<html xmlns="http://www.w3.org/1999/xhtml"></html>']
)
def test_an_icon_that_is_no_svg_document_raises_400(markup):
    error = icon_error(svg_base64(markup))
    assert error.status_code == 400
    assert error.message == "Field 'icon' must be an SVG document."


def test_an_icon_without_the_svg_namespace_names_it():
    error = icon_error(svg_base64('<svg viewBox="0 0 16 16"></svg>'))
    assert error.message == (
        "Field 'icon' needs xmlns=\"http://www.w3.org/2000/svg\" on its svg element."
    )


@pytest.mark.parametrize(
    "root",
    ['width="16" height="16"', 'viewBox="0 0 0 0"', 'viewBox="0 0 16"', 'viewBox="a b c d"'],
)
def test_an_icon_needs_a_view_box_with_an_area(root):
    assert icon_error(svg(root=root)).message == (
        "Field 'icon' needs a viewBox with a positive width and height."
    )


def test_a_comma_separated_view_box_is_accepted():
    assert decode_icon(svg(root='viewBox="0,0,24,24"')).startswith("data:image/svg+xml")


@pytest.mark.parametrize(
    "body",
    [
        '<rect width="4" height="4"><animate attributeName="x" to="8" dur="1s"/></rect>',
        '<rect width="4" height="4"><animateTransform attributeName="transform"/></rect>',
        '<rect width="4" height="4"><set attributeName="x" to="8"/></rect>',
        "<style>@keyframes spin { to { transform: rotate(1turn) } }</style>",
    ],
)
def test_an_animated_icon_raises_400(body):
    assert icon_error(svg(body)).message == "Field 'icon' must not be animated."


@pytest.mark.parametrize(
    "body", ['<text x="0" y="12">AI</text>', '<foreignObject width="16" height="16"/>']
)
def test_an_icon_with_text_raises_400(body):
    assert icon_error(svg(body)).message == (
        "Field 'icon' must not contain text. Convert it to paths first."
    )


def test_an_icon_with_a_plain_style_element_is_accepted():
    body = '<style>.a { fill: #000 }</style><rect class="a" width="16" height="16"/>'
    assert decode_icon(svg(body)).startswith("data:image/svg+xml")


def test_an_external_entity_is_not_resolved():
    markup = (
        '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY secret SYSTEM "file:///etc/passwd">]>'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><desc>&secret;</desc></svg>'
    )
    assert icon_error(svg_base64(markup)).message == "Field 'icon' must be an SVG document."


def test_oversized_icon_raises_413():
    padding = "<!--" + "x" * 300_000 + "-->"
    error = icon_error(svg(padding))
    assert error.status_code == 413
    assert error.message == "Field 'icon' must be at most 256 KB."


def test_the_icon_limit_never_exceeds_the_image_limit(monkeypatch):
    monkeypatch.setattr(settings, "max_image_bytes", 65_536)
    error = icon_error(svg("<!--" + "x" * 70_000 + "-->"))
    assert error.status_code == 413
    assert error.message == "Field 'icon' must be at most 64 KB."
