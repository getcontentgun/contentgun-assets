import logging
import sys
from collections.abc import Awaitable
from typing import Annotated, Any, Literal, TypeVar

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from cards import (
    CAROUSEL_CLOSING,
    CAROUSEL_CONTENT,
    CAROUSEL_COVER,
    CAROUSEL_WRITING,
    DEFAULT_PAGINATION,
    LIMITS,
    LIST_TEMPLATES,
    MAX_CAROUSEL_SLIDES,
    CarouselClosing,
    CarouselCover,
)
from client import AssetsClient
from config import ConfigError, load_config
from errors import AssetsError
from tools import CarouselSlide, Rendering, Tools

logger = logging.getLogger(__name__)

T = TypeVar("T")

READS = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)

INSTRUCTIONS = """\
contentgun-assets renders social media cards and Instagram carousels as PNG files below \
{output_root}. list_templates shows the templates and their fields. render_card saves one card, \
render_carousel saves a whole carousel into one folder. Both return the saved paths and a preview \
image: look at it before you report the result."""

RENDER_CARD = f"""\
Render one card and save it as a PNG file. Returns the saved path and a preview.

{LIMITS} What reads well: a headline of about six words and a subline of about eight; the full \
limits fit, but look like a wall of text.

The handle is never filled in: pass the account's handle, for example @contentgun. \
template is a template id from list_templates, written bundle/template; without one the service \
uses its default template. Every template's description says what it is for and how to fill \
its fields. Fields other than headline, subline and handle go into extra_fields. image_path only \
works with a template that accepts an image, icon_path, an SVG pictogram, only with one that \
accepts an icon; list_templates reports both as accepts_image and accepts_icon.

For a carousel, use render_carousel."""

RENDER_CAROUSEL = f"""\
Render an Instagram carousel and save its slides as 01.png, 02.png and so on in one folder: the \
cover first, one content slide per entry in slides, the closing slide last. The slides use the \
templates {CAROUSEL_COVER}, {CAROUSEL_CONTENT} and {CAROUSEL_CLOSING} of one bundle. Every \
slide gets the same format and handle; the content and closing slides show the pagination, the \
cover shows a swipe arrow. Returns the saved paths and one preview image with all slides. With \
pdf, the same slides also go into carousel.pdf, one page each, which LinkedIn takes as a document \
post.

{CAROUSEL_WRITING}

A content slide can carry a graphic under its text: icon_path for an SVG pictogram, drawn in \
the accent color or, with icon_color 'text', in the text color, or image_path for a picture \
shown as it is. Give either every content slide an icon or none of them.

{LIMITS} A carousel has at most {MAX_CAROUSEL_SLIDES} slides; Instagram's publishing API takes \
at most 10."""


async def _run(call: Awaitable[T]) -> T:
    try:
        return await call
    except AssetsError as exc:
        raise ToolError(str(exc)) from None


def _content(rendering: Rendering) -> list[str | Image]:
    return [rendering.summary, Image(data=rendering.preview, format="jpeg")]


def build_server(tools: Tools) -> MCPServer:
    output_root = tools.config.output_root
    image_roots = ", ".join(str(root) for root in tools.config.image_roots)
    handle_description = "The account's handle, for example @contentgun. Never filled in for you."
    format_description = (
        "'4:5' (1080x1350), '1:1' (1080x1080) or '9:16' (1080x1920), as the template offers."
    )
    overwrite_description = (
        "Replace existing files. Without it, an existing file is an error and nothing is rendered."
    )

    server = MCPServer(
        "contentgun-assets", instructions=INSTRUCTIONS.format(output_root=output_root)
    )

    @server.tool(description=LIST_TEMPLATES, annotations=READS, structured_output=True)
    async def list_templates() -> dict[str, Any]:
        return await _run(tools.list_templates())

    @server.tool(description=RENDER_CARD, annotations=WRITES, structured_output=False)
    async def render_card(
        output_path: Annotated[
            str,
            Field(
                description=f"Where to save the PNG: relative to {output_root}, or an absolute "
                "path inside it. Must end in .png."
            ),
        ],
        headline: Annotated[
            str | None,
            Field(
                description="The headline, up to 140 characters, for a template that declares "
                "headline; list_templates says which templates require it."
            ),
        ] = None,
        subline: Annotated[
            str | None,
            Field(description="The second line, for a template that declares subline."),
        ] = None,
        handle: Annotated[str | None, Field(description=handle_description)] = None,
        template: Annotated[
            str | None,
            Field(
                description="A template id from list_templates, written bundle/template. "
                "Defaults to the service's default template."
            ),
        ] = None,
        format: Annotated[str | None, Field(description=format_description)] = None,
        image_path: Annotated[
            str | None,
            Field(
                description="An image for a template that accepts one, a file inside one "
                f"of these folders: {image_roots}."
            ),
        ] = None,
        icon_path: Annotated[
            str | None,
            Field(
                description="An SVG pictogram for a template that accepts an icon, a file "
                f"inside one of these folders: {image_roots}."
            ),
        ] = None,
        extra_fields: Annotated[
            dict[str, str] | None,
            Field(description="Further fields the template declares, such as body or cta."),
        ] = None,
        overwrite: Annotated[bool, Field(description=overwrite_description)] = False,
    ) -> list[str | Image]:
        rendering = await _run(
            tools.render_card(
                output_path=output_path,
                headline=headline,
                subline=subline,
                handle=handle,
                template=template,
                format=format,
                image_path=image_path,
                icon_path=icon_path,
                extra_fields=extra_fields,
                overwrite=overwrite,
            )
        )
        return _content(rendering)

    @server.tool(description=RENDER_CAROUSEL, annotations=WRITES, structured_output=False)
    async def render_carousel(
        output_dir: Annotated[
            str,
            Field(
                description=f"The folder for the slides: relative to {output_root}, or an "
                "absolute path inside it. Created if it does not exist."
            ),
        ],
        cover: Annotated[CarouselCover, Field(description="The first slide.")],
        slides: Annotated[
            list[CarouselSlide],
            Field(
                min_length=1,
                max_length=MAX_CAROUSEL_SLIDES - 2,
                description="The content slides between cover and closing slide, in order. "
                f"icon_path and image_path are files inside one of these folders: {image_roots}.",
            ),
        ],
        closing: Annotated[CarouselClosing, Field(description="The last slide.")],
        handle: Annotated[str | None, Field(description=handle_description)] = None,
        format: Annotated[
            str | None,
            Field(description="'4:5' (1080x1350, the default) or '1:1' (1080x1080)."),
        ] = None,
        pagination: Annotated[
            str,
            Field(
                description="The page indicator on content and closing slides, with {page} and "
                "{pages} as placeholders: '{page}/{pages}' gives 2/7, '{page} von {pages}' "
                "gives 2 von 7. An empty string leaves it out."
            ),
        ] = DEFAULT_PAGINATION,
        icon_color: Annotated[
            Literal["accent", "text"] | None,
            Field(
                description="The color of every icon: 'accent', the bundle's accent color and "
                "the default, or 'text', the text color."
            ),
        ] = None,
        overwrite: Annotated[bool, Field(description=overwrite_description)] = False,
        pdf: Annotated[
            bool,
            Field(
                description="Also save all slides as carousel.pdf, one page per slide, for a "
                "LinkedIn document post."
            ),
        ] = False,
        bundle: Annotated[
            str | None,
            Field(
                description="The bundle whose carousel templates render the slides, as listed "
                "by list_templates. Defaults to the server's configured bundle."
            ),
        ] = None,
    ) -> list[str | Image]:
        rendering = await _run(
            tools.render_carousel(
                output_dir=output_dir,
                cover=cover,
                slides=slides,
                closing=closing,
                handle=handle,
                format=format,
                pagination=pagination,
                icon_color=icon_color,
                overwrite=overwrite,
                pdf=pdf,
                bundle=bundle,
            )
        )
        return _content(rendering)

    return server


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config()
    except ConfigError as exc:
        sys.exit(f"contentgun-assets MCP server: {exc}")
    logger.info("Serving %s, writing below %s", config.base_url, config.output_root)
    build_server(Tools(config, AssetsClient(config))).run()
