import base64
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from client import AssetsClient
from config import Config
from errors import AssetsError
from paths import image_file, output_file, output_folder, write_file
from preview import contact_sheet, png_size, preview_jpeg

logger = logging.getLogger(__name__)

NAMED_FIELDS = frozenset({"template", "format", "image", "icon", "headline", "subline", "handle"})
PATH_PARAMETERS = {"image": "image_path", "icon": "icon_path"}
MAX_CAROUSEL_SLIDES = 20
CAROUSEL_COVER = "carousel-cover"
CAROUSEL_CONTENT = "carousel-content"
CAROUSEL_CLOSING = "carousel-closing"
DEFAULT_PAGINATION = "{page}/{pages}"
SLIDE_FILE = re.compile(r"(\d{2})\.png")
PDF_FILE = "carousel.pdf"


class CarouselCover(BaseModel):
    headline: str = Field(description="The hook that makes people swipe, about six words.")
    topic: str | None = Field(
        default=None,
        description="A short uppercase kicker above the hook, such as the subject area. "
        "Up to 60 characters.",
    )
    subline: str | None = Field(
        default=None,
        description="One line under the hook that sets up the carousel, about eight words. "
        "Up to 200 characters.",
    )


class CarouselSlide(BaseModel):
    headline: str = Field(description="The point this slide makes, a few words.")
    body: str | None = Field(
        default=None,
        description="One or two sentences that explain the point, up to 200 characters.",
    )
    icon_path: str | None = Field(
        default=None,
        description="An SVG pictogram under the text, drawn in one brand color.",
    )
    image_path: str | None = Field(
        default=None,
        description="A PNG, JPEG or WebP under the text, shown as it is. A slide takes either "
        "icon_path or image_path.",
    )


class CarouselClosing(BaseModel):
    headline: str = Field(description="The resolution or takeaway of the carousel.")
    cta: str | None = Field(
        default=None,
        description="An open question to the reader, up to 200 characters.",
    )


@dataclass(frozen=True)
class Rendering:
    summary: str
    preview: bytes


@dataclass(frozen=True)
class Graphic:
    key: str
    data: bytes

    @property
    def encoded(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


def _bytes(graphics: list[Graphic | None], key: str) -> int:
    return sum(len(graphic.data) for graphic in graphics if graphic and graphic.key == key)


def _present(values: dict[str, str | None]) -> dict[str, str]:
    return {key: value for key, value in values.items() if value is not None}


def _kilobytes(size: int) -> str:
    return f"{size / 1024:.0f} KB"


def _pagination(pattern: str) -> Callable[[int, int], str | None]:
    if not pattern:
        return lambda page, pages: None
    try:
        pattern.format(page=1, pages=1)
    except (KeyError, IndexError, ValueError):
        raise AssetsError(
            "pagination may only use the placeholders {page} and {pages}, "
            "for example '{page}/{pages}'."
        ) from None
    return lambda page, pages: pattern.format(page=page, pages=pages)


class Tools:
    def __init__(self, config: Config, client: AssetsClient):
        self.config = config
        self._client = client

    async def list_templates(self) -> dict[str, Any]:
        return {"templates": await self._client.list_templates()}

    async def render_card(
        self,
        *,
        output_path: str,
        headline: str,
        subline: str | None = None,
        handle: str | None = None,
        template: str | None = None,
        format: str | None = None,
        image_path: str | None = None,
        icon_path: str | None = None,
        extra_fields: dict[str, str] | None = None,
        overwrite: bool = False,
    ) -> Rendering:
        target = output_file(output_path, self.config.output_root, overwrite)
        graphic = self._graphic(image_path, icon_path)

        extra = dict(extra_fields or {})
        collisions = sorted(set(extra) & NAMED_FIELDS)
        if collisions:
            parameter = PATH_PARAMETERS.get(collisions[0], collisions[0])
            raise AssetsError(
                f"extra_fields must not contain {collisions[0]!r}. "
                f"Use the {parameter} parameter instead."
            )

        body: dict[str, Any] = _present(
            {
                "template": template,
                "format": format,
                "headline": headline,
                "subline": subline,
                "handle": handle,
            }
        )
        body.update(extra)
        if graphic is not None:
            body[graphic.key] = graphic.encoded

        rendered = await self._client.render(body)
        self._write(target, rendered.data)

        width, height = png_size(rendered.data)
        logger.info(
            "Rendered card template=%s format=%s headline_length=%d image_bytes=%d icon_bytes=%d "
            "request_id=%s",
            template or "default",
            format or "default",
            len(headline),
            _bytes([graphic], "image"),
            _bytes([graphic], "icon"),
            rendered.request_id,
        )
        summary = (
            f"Saved {target} ({width}x{height}, {_kilobytes(len(rendered.data))}, "
            f"request {rendered.request_id})."
        )
        return Rendering(summary=summary, preview=preview_jpeg(rendered.data))

    async def render_carousel(
        self,
        *,
        output_dir: str,
        cover: CarouselCover,
        slides: list[CarouselSlide],
        closing: CarouselClosing,
        handle: str | None = None,
        format: str | None = None,
        pagination: str = DEFAULT_PAGINATION,
        icon_color: str | None = None,
        overwrite: bool = False,
        pdf: bool = False,
        bundle: str | None = None,
    ) -> Rendering:
        bundle = bundle or self.config.bundle
        if not bundle:
            raise AssetsError(
                "render_carousel needs a bundle: pass bundle, or set ASSETS_BUNDLE in the "
                "server's configuration."
            )
        folder = output_folder(output_dir, self.config.output_root)
        if not slides:
            raise AssetsError("A carousel needs at least one content slide.")
        pages = len(slides) + 2
        if pages > MAX_CAROUSEL_SLIDES:
            raise AssetsError(
                f"A carousel has at most {MAX_CAROUSEL_SLIDES} slides, "
                "cover and closing slide included."
            )
        label = _pagination(pagination)

        targets = [folder / f"{number:02d}.png" for number in range(1, pages + 1)]
        document = folder / PDF_FILE
        files = [*targets, document] if pdf else targets
        folders = [file.name for file in files if file.is_dir()]
        if folders:
            raise AssetsError(f"{folder} holds folders named {', '.join(folders)}.")
        taken = [file.name for file in files if file.exists()]
        if taken and not overwrite:
            raise AssetsError(
                f"{folder} already holds {', '.join(taken)}. Set overwrite to true to replace them."
            )

        graphics: list[Graphic | None] = []
        for page, slide in enumerate(slides, start=2):
            try:
                graphics.append(self._graphic(slide.image_path, slide.icon_path))
            except AssetsError as exc:
                raise AssetsError(f"Slide {page}: {exc}") from None

        shared = _present({"format": format, "handle": handle})
        bodies: list[dict[str, str]] = [
            {
                "template": f"{bundle}/{CAROUSEL_COVER}",
                **_present(
                    {"headline": cover.headline, "topic": cover.topic, "subline": cover.subline}
                ),
                **shared,
            }
        ]
        for page, (slide, graphic) in enumerate(zip(slides, graphics, strict=True), start=2):
            body = {
                "template": f"{bundle}/{CAROUSEL_CONTENT}",
                **_present(
                    {
                        "headline": slide.headline,
                        "body": slide.body,
                        "pagination": label(page, pages),
                    }
                ),
                **shared,
            }
            if graphic is not None:
                body[graphic.key] = graphic.encoded
                if graphic.key == "icon" and icon_color:
                    body["icon_color"] = icon_color
            bodies.append(body)
        bodies.append(
            {
                "template": f"{bundle}/{CAROUSEL_CLOSING}",
                **_present(
                    {
                        "headline": closing.headline,
                        "cta": closing.cta,
                        "pagination": label(pages, pages),
                    }
                ),
                **shared,
            }
        )

        # Render every slide before writing any, so a failing slide leaves the folder untouched.
        pngs: list[bytes] = []
        for number, body in enumerate(bodies, start=1):
            try:
                rendered = await self._client.render(body)
            except AssetsError as exc:
                raise AssetsError(
                    f"Slide {number} ({body['template']}) failed and nothing was saved: {exc}"
                ) from None
            pngs.append(rendered.data)
        pdf_data = None
        if pdf:
            try:
                pdf_data = (await self._client.document(bodies)).data
            except AssetsError as exc:
                raise AssetsError(f"The PDF failed and nothing was saved: {exc}") from None

        for target, png in zip(targets, pngs, strict=True):
            self._write(target, png)
        if pdf_data is not None:
            self._write(document, pdf_data)

        width, height = png_size(pngs[0])
        logger.info(
            "Rendered carousel slides=%d format=%s pdf=%s headline_lengths=%s image_bytes=%d "
            "icon_bytes=%d",
            pages,
            format or "default",
            pdf,
            ",".join(str(len(body["headline"])) for body in bodies),
            _bytes(graphics, "image"),
            _bytes(graphics, "icon"),
        )
        lines = [f"Saved {pages} slides in {folder} ({width}x{height}):"]
        for target, body in zip(targets, bodies, strict=True):
            suffix = f", pagination {body['pagination']}" if "pagination" in body else ""
            suffix += "".join(f", {key}" for key in ("image", "icon") if key in body)
            lines.append(f"- {target.name}: {body['template']}{suffix}")
        if pdf_data is not None:
            lines.append(f"- {PDF_FILE}: all {pages} slides, one page each")
        stale = self._stale_slides(folder, pages)
        if stale:
            lines.append(
                f"Older slides from an earlier run are still in the folder: {', '.join(stale)}."
            )
        return Rendering(summary="\n".join(lines), preview=contact_sheet(pngs))

    def _graphic(self, image_path: str | None, icon_path: str | None) -> Graphic | None:
        if image_path and icon_path:
            raise AssetsError("Pass either image_path or icon_path, not both.")
        key, path = ("icon", icon_path) if icon_path else ("image", image_path)
        if not path:
            return None
        source = image_file(path, self.config.image_roots, key)
        try:
            return Graphic(key, source.read_bytes())
        except OSError as exc:
            reason = exc.strerror or exc
            raise AssetsError(f"Could not read the {key} {source}: {reason}.") from None

    def _write(self, target: Path, data: bytes) -> None:
        try:
            write_file(target, data)
        except OSError as exc:
            raise AssetsError(f"Could not write {target}: {exc.strerror or exc}.") from None

    @staticmethod
    def _stale_slides(folder: Path, pages: int) -> list[str]:
        stale = []
        for path in folder.iterdir():
            match = SLIDE_FILE.fullmatch(path.name)
            if match and int(match.group(1)) > pages:
                stale.append(path.name)
        return sorted(stale)
