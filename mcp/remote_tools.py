import base64
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import Field

from cards import (
    CAROUSEL_TEMPLATES,
    DEFAULT_PAGINATION,
    CarouselClosing,
    CarouselCover,
    SlideText,
    card_body,
    carousel_bodies,
    carousel_pages,
    pagination_label,
)
from client import AssetsClient, Stored, Wording
from errors import AssetsError
from preview import contact_sheet, preview_jpeg

logger = logging.getLogger(__name__)

REMOTE = Wording(
    timeout="The ContentGun service did not answer within {seconds:g} seconds. Retrying is safe.",
    unreachable="The ContentGun service cannot be reached right now. Retrying in a minute is safe.",
    rejected="The ContentGun service refused this connector; its operator has to fix that.",
    unexpected="The ContentGun service answered unexpectedly, with HTTP {status}.",
)

# Every key of RESERVED_KEYS in app/schemas.py: extra_fields setting output, for instance, would
# turn the stored result into a file in the response.
RESERVED: dict[str, str | None] = {
    "template": "template",
    "format": "format",
    "headline": "headline",
    "subline": "subline",
    "handle": "handle",
    "image": "image_url or image_upload",
    "image_url": "image_url",
    "image_upload": "image_upload",
    "icon": "icon_svg, icon_url or icon_upload",
    "icon_url": "icon_url",
    "icon_upload": "icon_upload",
    "code": "code",
    "language": "language",
    "line_numbers": "line_numbers",
    "highlight_lines": "highlight_lines",
    "output": None,
}
GRAPHICS = ("image_url", "image_upload", "icon_svg", "icon_url", "icon_upload")
NO_PREVIEW = "There is no preview, but the URL works."
DOWNLOAD = (
    "To show the result in the chat, run this in your code execution environment and share the "
    "downloaded files with the user:"
)


class ContentSlide(SlideText):
    image_url: str | None = Field(
        default=None,
        description="A picture under the text: a public https address of a PNG, JPEG or WebP.",
    )
    image_upload: str | None = Field(
        default=None,
        description="A picture under the text: the upload_id of a PNG, JPEG or WebP from "
        "create_upload.",
    )
    icon_svg: str | None = Field(
        default=None,
        description="A pictogram under the text: the markup of an SVG you draw yourself, with a "
        "viewBox and without text.",
    )
    icon_url: str | None = Field(
        default=None,
        description="A pictogram under the text: a public https address of an SVG.",
    )
    icon_upload: str | None = Field(
        default=None,
        description="A pictogram under the text: the upload_id of an SVG from create_upload. A "
        "slide takes one picture or one pictogram.",
    )


@dataclass(frozen=True)
class Result:
    summary: str
    preview: bytes | None


def graphic_fields(
    image_url: str | None = None,
    image_upload: str | None = None,
    icon_svg: str | None = None,
    icon_url: str | None = None,
    icon_upload: str | None = None,
) -> dict[str, str]:
    """The body keys of the one image or icon a card or slide gets, or none."""
    values = (image_url, image_upload, icon_svg, icon_url, icon_upload)
    given = {name: value for name, value in zip(GRAPHICS, values, strict=True) if value}
    if len(given) > 1:
        raise AssetsError(f"Pass one image or icon, not {' and '.join(given)} together.")
    if not given:
        return {}
    [(name, value)] = given.items()
    if name == "icon_svg":
        return {"icon": base64.b64encode(value.encode("utf-8")).decode("ascii")}
    return {name: value}


def _source(body: dict[str, Any]) -> str:
    if body.keys() & {"image", "image_url", "image_upload"}:
        return "image"
    if body.keys() & {"icon", "icon_url", "icon_upload"}:
        return "icon"
    return "none"


def _kilobytes(size: int) -> str:
    return f"{size / 1024:.0f} KB"


def _download(files: list[tuple[str, str]]) -> str:
    """One curl command that saves every URL under its name, in order."""
    return "curl -fsS " + " ".join(f'-o {name} "{url}"' for name, url in files)


class RemoteTools:
    """The tools of the remote server, independent of the SDK. The tenant comes from the token
    of the caller, never from a tool argument."""

    def __init__(self, client: AssetsClient):
        self._client = client

    async def list_templates(self, tenant: str) -> dict[str, Any]:
        return {"templates": await self._client.for_tenant(tenant).list_templates()}

    async def create_upload(self, tenant: str) -> dict[str, Any]:
        upload = await self._client.for_tenant(tenant).create_upload()
        logger.info("Handed out an upload tenant=%s upload=%s", tenant, upload.upload_id)
        return {
            "upload_id": upload.upload_id,
            "upload_url": upload.upload_url,
            "expires_at": upload.expires_at,
            "max_bytes": upload.max_bytes,
            "command": f'curl -sS -T FILE "{upload.upload_url}"',
        }

    async def render_card(
        self,
        tenant: str,
        *,
        headline: str | None = None,
        subline: str | None = None,
        handle: str | None = None,
        template: str | None = None,
        format: str | None = None,
        extra_fields: dict[str, str] | None = None,
        image_url: str | None = None,
        image_upload: str | None = None,
        icon_svg: str | None = None,
        icon_url: str | None = None,
        icon_upload: str | None = None,
        code: str | None = None,
        language: str | None = None,
        line_numbers: bool | None = None,
        highlight_lines: list[int] | None = None,
    ) -> Result:
        client = self._client.for_tenant(tenant)
        graphic = graphic_fields(image_url, image_upload, icon_svg, icon_url, icon_upload)
        body = card_body(
            headline=headline,
            subline=subline,
            handle=handle,
            template=template,
            format=format,
            extra_fields=extra_fields,
            reserved=RESERVED,
            code=code,
            language=language,
            line_numbers=line_numbers,
            highlight_lines=highlight_lines,
        )
        body.update(graphic)
        stored = await client.render_stored(body)
        try:
            preview = preview_jpeg(await client.stored_file(stored.url))
        except (AssetsError, OSError):
            preview = None
        logger.info(
            "Rendered card tenant=%s template=%s format=%s headline_length=%d source=%s "
            "request_id=%s",
            tenant,
            template or "default",
            format or "default",
            len(headline or ""),
            _source(graphic),
            stored.request_id,
        )
        lines = [
            f"Rendered the card ({stored.width}x{stored.height}, {_kilobytes(stored.size)}): "
            f"{stored.url}",
            f"The URL works until {stored.expires_at}.",
        ]
        if preview is None:
            lines.append(NO_PREVIEW)
        lines += [DOWNLOAD, f'curl -fsS -O "{stored.url}"']
        return Result("\n".join(lines), preview)

    async def render_carousel(
        self,
        tenant: str,
        *,
        cover: CarouselCover,
        slides: list[ContentSlide],
        closing: CarouselClosing,
        handle: str | None = None,
        format: str | None = None,
        pagination: str = DEFAULT_PAGINATION,
        icon_color: str | None = None,
        pdf: bool = False,
        bundle: str | None = None,
    ) -> Result:
        client = self._client.for_tenant(tenant)
        pages = carousel_pages(slides)
        label = pagination_label(pagination)
        graphics = []
        for page, slide in enumerate(slides, start=2):
            try:
                graphics.append(
                    graphic_fields(
                        slide.image_url,
                        slide.image_upload,
                        slide.icon_svg,
                        slide.icon_url,
                        slide.icon_upload,
                    )
                )
            except AssetsError as exc:
                raise AssetsError(f"Slide {page}: {exc}") from None
        bundle = bundle or await self._carousel_bundle(client)
        bodies = carousel_bodies(
            bundle=bundle,
            cover=cover,
            slides=slides,
            closing=closing,
            graphics=graphics,
            label=label,
            handle=handle,
            format=format,
            icon_color=icon_color,
        )

        results: list[Stored] = []
        for number, body in enumerate(bodies, start=1):
            try:
                results.append(await client.render_stored(body))
            except AssetsError as exc:
                raise AssetsError(f"Slide {number} ({body['template']}) failed: {exc}") from None
        document = None
        if pdf:
            try:
                document = await client.document_stored(bodies)
            except AssetsError as exc:
                raise AssetsError(f"The slides were rendered, but the PDF failed: {exc}") from None
        try:
            sheet = contact_sheet([await client.stored_file(stored.url) for stored in results])
        except (AssetsError, OSError):
            sheet = None

        logger.info(
            "Rendered carousel tenant=%s bundle=%s slides=%d format=%s pdf=%s",
            tenant,
            bundle,
            pages,
            format or "default",
            pdf,
        )
        first = results[0]
        lines = [f"Rendered {pages} slides ({first.width}x{first.height}), in order:"]
        for number, (stored, body) in enumerate(zip(results, bodies, strict=True), start=1):
            details = [body["template"]]
            if "pagination" in body:
                details.append(f"pagination {body['pagination']}")
            if _source(body) != "none":
                details.append(_source(body))
            lines.append(f"{number}. {stored.url} ({', '.join(details)})")
        if document is not None:
            lines.append(f"PDF with all {document.pages} slides, one page each: {document.url}")
        lines.append(f"The URLs work until {min(stored.expires_at for stored in results)}.")
        if sheet is None:
            lines.append(NO_PREVIEW)
        stem = f"carousel-{first.request_id[:8]}"
        files = [
            (f"{stem}-{number:02d}.png", stored.url)
            for number, stored in enumerate(results, start=1)
        ]
        if document is not None:
            files.append((f"{stem}.pdf", document.url))
        lines += [DOWNLOAD, _download(files)]
        return Result("\n".join(lines), sheet)

    async def _carousel_bundle(self, client: AssetsClient) -> str:
        """The one bundle of the tenant with all three carousel templates."""
        ids = {
            entry.get("id") for entry in await client.list_templates() if isinstance(entry, dict)
        }
        bundles = sorted({value.split("/")[0] for value in ids if isinstance(value, str)})
        offering = [
            name
            for name in bundles
            if all(f"{name}/{template}" in ids for template in CAROUSEL_TEMPLATES)
        ]
        if len(offering) == 1:
            return offering[0]
        if not offering:
            raise AssetsError(
                "None of your bundles offers carousels, which takes the templates "
                f"{', '.join(CAROUSEL_TEMPLATES)}."
            )
        raise AssetsError(
            f"Several of your bundles offer carousels: {', '.join(offering)}. Pass one as bundle."
        )
