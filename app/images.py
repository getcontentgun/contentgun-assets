import base64
import binascii
import math
import pyexpat
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from app.config import settings
from app.errors import ApiError

DATA_URI_PREFIX = re.compile(
    r"^data:image/[a-z0-9.+-]+(?:;[a-z0-9.+-]+=[^;,]*)*;base64,", re.IGNORECASE
)
WHITESPACE = re.compile(r"\s+")

SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
RIFF_HEADER = b"RIFF"
WEBP_TAG = b"WEBP"

ACCEPTED = "PNG, JPEG or WebP"

MAX_ICON_BYTES = 262_144
SVG_TYPE = "image/svg+xml"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
SVG_ROOT = f"{{{SVG_NAMESPACE}}}svg"
SVG_STYLE = f"{{{SVG_NAMESPACE}}}style"
# An animated icon is captured at whatever frame the screenshot happens to hit, and text is
# set in the host's system fonts, which differ between macOS and the production browser.
ANIMATION_ELEMENTS = frozenset(
    f"{{{SVG_NAMESPACE}}}{name}" for name in ("animate", "animateMotion", "animateTransform", "set")
)
TEXT_ELEMENTS = frozenset(f"{{{SVG_NAMESPACE}}}{name}" for name in ("text", "foreignObject"))
KEYFRAMES = re.compile(r"@(?:-webkit-)?keyframes\b", re.IGNORECASE)
VIEW_BOX_SEPARATOR = re.compile(r"[\s,]+")

# Older expat expands nested entities without limit, which turns a small icon into gigabytes.
MIN_EXPAT = (2, 4, 1)


def readable_size(value: int) -> str:
    if value < 1_048_576:
        return f"{value / 1024:g} KB"
    return f"{value / 1_048_576:g} MB"


def _sniff(data: bytes) -> str | None:
    for signature, mime in SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == RIFF_HEADER and data[8:12] == WEBP_TAG:
        return "image/webp"
    return None


def _looks_like_markup(data: bytes) -> bool:
    return data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] == b"<"


def _data_uri(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def icon_limit() -> int:
    return min(MAX_ICON_BYTES, settings.max_image_bytes)


def verify_xml_parser() -> None:
    if pyexpat.version_info < MIN_EXPAT:
        raise RuntimeError(
            f"{pyexpat.EXPAT_VERSION} cannot parse icons safely; expat 2.4.1 or newer is required."
        )


@lru_cache(maxsize=4)
def file_data_uri(path: Path) -> str:
    """Cached: a fallback image is a few hundred kilobytes and never changes at runtime."""
    data = path.read_bytes()
    mime = _sniff(data)
    if mime is None:
        raise RuntimeError(f"Fallback image {path} is not a {ACCEPTED} file.")
    return _data_uri(mime, data)


def data_uri_size(uri: str | None) -> int:
    if not uri:
        return 0
    _, _, payload = uri.partition(",")
    return len(payload) // 4 * 3 - payload.count("=")


def _payload(raw: str) -> str:
    return WHITESPACE.sub("", DATA_URI_PREFIX.sub("", raw.strip()))


def estimated_size(raw: str | None) -> int:
    if raw is None:
        return 0
    return len(_payload(raw)) * 3 // 4


def _decode(raw: str, field: str, limit: int, kind: str) -> bytes:
    payload = _payload(raw)

    # Estimated from the encoded string first: decoding an oversized payload to then
    # reject it is the exact work the limit exists to avoid.
    if len(payload) * 3 // 4 > limit:
        raise ApiError(413, f"Field {field!r} must be at most {readable_size(limit)}.")

    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(400, f"Field {field!r} must be base64-encoded {kind} data.") from None

    if len(data) > limit:
        raise ApiError(413, f"Field {field!r} must be at most {readable_size(limit)}.")
    return data


def image_data_uri(data: bytes, subject: str = "Field 'image'", other: str = "icon") -> str:
    """The image as a data URI, re-encoded from the bytes that were checked, so the page can only
    ever receive what passed validation."""
    mime = _sniff(data)
    if mime is None:
        hint = f" Send an SVG as {other!r}." if _looks_like_markup(data) else ""
        raise ApiError(400, f"{subject} must be a {ACCEPTED} image.{hint}")
    return _data_uri(mime, data)


def decode_image(raw: str) -> str:
    return image_data_uri(_decode(raw, "image", settings.max_image_bytes, "image"))


def _has_area(view_box: str | None) -> bool:
    try:
        numbers = [float(part) for part in VIEW_BOX_SEPARATOR.split((view_box or "").strip())]
    except ValueError:
        return False
    return (
        len(numbers) == 4
        and all(math.isfinite(number) for number in numbers)
        and numbers[2] > 0
        and numbers[3] > 0
    )


def _check_svg(root: ElementTree.Element, subject: str) -> None:
    if root.tag == "svg":
        raise ApiError(400, f'{subject} needs xmlns="{SVG_NAMESPACE}" on its svg element.')
    if root.tag != SVG_ROOT:
        raise ApiError(400, f"{subject} must be an SVG document.")
    if not _has_area(root.get("viewBox")):
        raise ApiError(400, f"{subject} needs a viewBox with a positive width and height.")
    for element in root.iter():
        if element.tag in ANIMATION_ELEMENTS or (
            element.tag == SVG_STYLE and KEYFRAMES.search(element.text or "")
        ):
            raise ApiError(400, f"{subject} must not be animated.")
        if element.tag in TEXT_ELEMENTS:
            raise ApiError(400, f"{subject} must not contain text. Convert it to paths first.")


def icon_data_uri(data: bytes, subject: str = "Field 'icon'", other: str = "image") -> str:
    if _sniff(data) is not None:
        raise ApiError(400, f"{subject} must be an SVG document. Send a {ACCEPTED} as {other!r}.")

    # The stdlib parser is safe here because verify_xml_parser() refuses an expat that
    # still expands entities without limit, and the size cap bounds everything else.
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        raise ApiError(400, f"{subject} must be an SVG document.") from None
    _check_svg(root, subject)

    return _data_uri(SVG_TYPE, data)


def decode_icon(raw: str) -> str:
    if raw.lstrip().startswith("<"):
        raise ApiError(400, "Field 'icon' must be base64-encoded. Encode the SVG markup first.")
    return icon_data_uri(_decode(raw, "icon", icon_limit(), "SVG"))


def classify_upload(data: bytes) -> tuple[Literal["image", "icon"], str]:
    """Whether an upload is an image or an icon, and its media type, after the same checks a
    request's image or icon gets."""
    if not data:
        raise ApiError(400, "The upload is empty.")
    mime = _sniff(data)
    if mime is not None:
        return "image", mime
    if not _looks_like_markup(data):
        raise ApiError(400, f"The upload must be a {ACCEPTED} image or an SVG icon.")
    if len(data) > icon_limit():
        raise ApiError(413, f"An SVG icon must be at most {readable_size(icon_limit())}.")
    icon_data_uri(data, "The uploaded SVG")
    return "icon", SVG_TYPE
