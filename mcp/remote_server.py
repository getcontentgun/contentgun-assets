import argparse
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypeVar
from urllib.parse import urlsplit

import httpx
import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from accounts import Accounts
from cards import (
    CAROUSEL_CLOSING,
    CAROUSEL_CONTENT,
    CAROUSEL_COVER,
    CAROUSEL_WRITING,
    CODE,
    CODE_FIELD,
    DEFAULT_PAGINATION,
    HIGHLIGHT_LINES_FIELD,
    LANGUAGE_FIELD,
    LIMITS,
    LINE_NUMBERS_FIELD,
    LIST_TEMPLATES,
    MAX_CAROUSEL_SLIDES,
    CarouselClosing,
    CarouselCover,
)
from client import AssetsClient
from errors import AssetsError, ConfigError
from login import Logins
from login_pages import add_login_routes
from mailer import Mailer, mailer_for
from oauth import REQUIRED_SCOPE, SCOPES, Provider
from remote_config import RemoteConfig, load_remote_config
from remote_tools import REMOTE, ContentSlide, RemoteTools, Result
from version import __version__

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_PORT = 8020
CLAUDE_ORIGINS = ("https://claude.ai", "https://claude.com")
GRACEFUL_SHUTDOWN_S = 3
KEEP_ALIVE_S = 130
QUIET_LOGGERS = ("httpx", "mcp")

INSTRUCTIONS = """\
ContentGun renders social media cards and Instagram carousels with the templates of the \
signed-in team. list_templates shows the templates and their fields. render_card renders one \
card, render_carousel a whole carousel; both return URLs and a preview image: look at the \
preview before you report the result, and give the user the URLs. To put an image file from the \
conversation on a card, call create_upload, run the curl command it returns in your code \
execution environment, then pass the upload_id. whoami shows which address and team are signed \
in."""

CREATE_UPLOAD = """\
Get a URL that takes one image or icon file for a card. Run the returned command in your code \
execution environment, with the path of the file in place of FILE; files the user attached to \
the conversation are usually below /mnt/user-data/uploads/. Then pass upload_id to render_card \
or to a slide of render_carousel: as image_upload for a PNG, JPEG or WebP, as icon_upload for an \
SVG. The URL takes one file, up to max_bytes, within 15 minutes. If the command cannot reach the \
host, the code execution environment may not reach this domain: tell the user that it has to be \
added under "Additional allowed domains" in the settings of Claude, and show the exact output of \
the command."""

GRAPHIC_SOURCES = """\
A template that accepts an image takes image_url, a public https address of a PNG, JPEG or WebP, \
or image_upload, the upload_id of a file sent with create_upload. A template that accepts an \
icon takes icon_svg, the markup of an SVG pictogram you draw yourself with a viewBox and without \
text, icon_url or icon_upload. list_templates reports both as accepts_image and accepts_icon. \
Pass at most one of these five."""

RENDER_CARD = f"""\
Render one card as a PNG. Returns its URL and a preview.

{LIMITS} What reads well: a headline of about six words and a subline of about eight; the full \
limits fit, but look like a wall of text.

The handle is never filled in: pass the account's handle, for example @contentgun. \
template is a template id from list_templates, written bundle/template; without one the service \
uses the team's default template. Every template's description says what it is for and how to \
fill its fields. Fields other than headline, subline and handle go into extra_fields.

{GRAPHIC_SOURCES}

{CODE}

For a carousel, use render_carousel."""

RENDER_CAROUSEL = f"""\
Render an Instagram carousel: the cover first, one content slide per entry in slides, the \
closing slide last. The slides use the templates {CAROUSEL_COVER}, {CAROUSEL_CONTENT} and \
{CAROUSEL_CLOSING} of one bundle. Every slide gets the same format and handle; the content and \
closing slides show the pagination, the cover shows a swipe arrow. Returns the URL of every \
slide, in order, and one preview image with all slides. With pdf, the same slides also come as \
one PDF, one page each, which LinkedIn takes as a document post.

{CAROUSEL_WRITING}

A content slide can carry a graphic under its text: a pictogram from icon_svg, icon_url or \
icon_upload, drawn in the accent color or, with icon_color 'text', in the text color; or a \
picture shown as it is, from image_url or image_upload. Give either every content slide a \
pictogram or none of them.

{LIMITS} A carousel has at most {MAX_CAROUSEL_SLIDES} slides; Instagram's publishing API takes \
at most 10."""

READS = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
WHOAMI = ToolAnnotations(read_only_hint=True, open_world_hint=False)
HANDLE = "The account's handle, for example @contentgun. Never filled in for you."
FORMAT = "'4:5' (1080x1350), '1:1' (1080x1080) or '9:16' (1080x1920), as the template offers."


def _moment(seconds: Any) -> str | None:
    if not isinstance(seconds, int | float):
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _hosts(public_url: str) -> list[str]:
    """The Host headers the public address arrives with, with and without the default port."""
    parts = urlsplit(public_url)
    default = 443 if parts.scheme == "https" else 80
    host = parts.netloc.rsplit(":", 1)[0] if parts.port is not None else parts.netloc
    hosts = [parts.netloc]
    if parts.port is None:
        hosts.append(f"{parts.netloc}:{default}")
    elif parts.port == default:
        hosts.append(host)
    return hosts


def _tenant() -> str:
    token = get_access_token()
    tenant = None if token is None else (token.claims or {}).get("tenant")
    if not isinstance(tenant, str):
        raise ToolError("Nobody is signed in.")
    return tenant


async def _run(call: Awaitable[T]) -> T:
    try:
        return await call
    except AssetsError as exc:
        raise ToolError(str(exc)) from None


def _content(result: Result) -> list[str | Image]:
    if result.preview is None:
        return [result.summary]
    return [result.summary, Image(data=result.preview, format="jpeg")]


def _add_tools(server: MCPServer, tools: RemoteTools) -> None:
    @server.tool(
        description="Show which address and team are signed in to ContentGun, and until when.",
        annotations=WHOAMI,
        structured_output=True,
    )
    async def whoami() -> dict[str, Any]:
        token = get_access_token()
        if token is None:
            raise ToolError("Nobody is signed in.")
        claims = token.claims or {}
        return {
            "email": token.subject,
            "tenant": claims.get("tenant"),
            "signed_in_at": _moment(claims.get("signed_in_at")),
            "access_expires_at": _moment(token.expires_at),
        }

    @server.tool(description=LIST_TEMPLATES, annotations=READS, structured_output=True)
    async def list_templates() -> dict[str, Any]:
        return await _run(tools.list_templates(_tenant()))

    @server.tool(description=CREATE_UPLOAD, annotations=WRITES, structured_output=True)
    async def create_upload() -> dict[str, Any]:
        return await _run(tools.create_upload(_tenant()))

    @server.tool(description=RENDER_CARD, annotations=WRITES, structured_output=False)
    async def render_card(
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
        handle: Annotated[str | None, Field(description=HANDLE)] = None,
        template: Annotated[
            str | None,
            Field(
                description="A template id from list_templates, written bundle/template. "
                "Defaults to the team's default template."
            ),
        ] = None,
        format: Annotated[str | None, Field(description=FORMAT)] = None,
        image_url: Annotated[
            str | None,
            Field(description="A public https address of a PNG, JPEG or WebP."),
        ] = None,
        image_upload: Annotated[
            str | None,
            Field(description="The upload_id of a PNG, JPEG or WebP sent with create_upload."),
        ] = None,
        icon_svg: Annotated[
            str | None,
            Field(
                description="The markup of an SVG pictogram you draw yourself, with a viewBox "
                "and without text."
            ),
        ] = None,
        icon_url: Annotated[
            str | None, Field(description="A public https address of an SVG pictogram.")
        ] = None,
        icon_upload: Annotated[
            str | None, Field(description="The upload_id of an SVG sent with create_upload.")
        ] = None,
        code: Annotated[str | None, Field(description=CODE_FIELD)] = None,
        language: Annotated[str | None, Field(description=LANGUAGE_FIELD)] = None,
        line_numbers: Annotated[bool | None, Field(description=LINE_NUMBERS_FIELD)] = None,
        highlight_lines: Annotated[
            list[int] | None, Field(description=HIGHLIGHT_LINES_FIELD)
        ] = None,
        extra_fields: Annotated[
            dict[str, str] | None,
            Field(description="Further fields the template declares, such as body or cta."),
        ] = None,
    ) -> list[str | Image]:
        result = await _run(
            tools.render_card(
                _tenant(),
                headline=headline,
                subline=subline,
                handle=handle,
                template=template,
                format=format,
                extra_fields=extra_fields,
                image_url=image_url,
                image_upload=image_upload,
                icon_svg=icon_svg,
                icon_url=icon_url,
                icon_upload=icon_upload,
                code=code,
                language=language,
                line_numbers=line_numbers,
                highlight_lines=highlight_lines,
            )
        )
        return _content(result)

    @server.tool(description=RENDER_CAROUSEL, annotations=WRITES, structured_output=False)
    async def render_carousel(
        cover: Annotated[CarouselCover, Field(description="The first slide.")],
        slides: Annotated[
            list[ContentSlide],
            Field(
                min_length=1,
                max_length=MAX_CAROUSEL_SLIDES - 2,
                description="The content slides between cover and closing slide, in order.",
            ),
        ],
        closing: Annotated[CarouselClosing, Field(description="The last slide.")],
        handle: Annotated[str | None, Field(description=HANDLE)] = None,
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
                description="The color of every pictogram: 'accent', the bundle's accent color "
                "and the default, or 'text', the text color."
            ),
        ] = None,
        pdf: Annotated[
            bool,
            Field(
                description="Also render all slides as one PDF, one page per slide, for a "
                "LinkedIn document post."
            ),
        ] = False,
        bundle: Annotated[
            str | None,
            Field(
                description="The bundle whose carousel templates render the slides, as listed "
                "by list_templates. Needed only when several of the team's bundles offer "
                "carousels."
            ),
        ] = None,
    ) -> list[str | Image]:
        result = await _run(
            tools.render_carousel(
                _tenant(),
                cover=cover,
                slides=slides,
                closing=closing,
                handle=handle,
                format=format,
                pagination=pagination,
                icon_color=icon_color,
                pdf=pdf,
                bundle=bundle,
            )
        )
        return _content(result)


def build_app(
    config: RemoteConfig,
    *,
    mailer: Mailer | None = None,
    clock: Callable[[], float] = time.time,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Starlette:
    """The ASGI app. Raises ConfigError when the tenants file is invalid."""
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    if config.tenants_file is not None:
        accounts = Accounts.from_file(config.tenants_file)
    else:
        accounts = Accounts.from_addresses(config.emails)
    logins = Logins(accounts, clock)
    provider = Provider(config, accounts, logins, clock)
    server = MCPServer(
        "contentgun",
        title="ContentGun",
        instructions=INSTRUCTIONS,
        version=__version__,
        log_level="WARNING",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=config.public_url,
            resource_server_url=config.resource_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=list(SCOPES), default_scopes=list(SCOPES)
            ),
            revocation_options=RevocationOptions(enabled=False),
            required_scopes=[REQUIRED_SCOPE],
            validate_token_resource=True,
        ),
    )
    _add_tools(server, RemoteTools(AssetsClient(config, transport, wording=REMOTE)))
    add_login_routes(server, logins, provider.finish, config, mailer or mailer_for(config))

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True, "service": "contentgun-mcp", "version": __version__})

    return server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_hosts(config.public_url),
            allowed_origins=[config.origin, *CLAUDE_ORIGINS],
        ),
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="The remote MCP server of ContentGun.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_remote_config()
        app = build_app(config)
    except ConfigError as exc:
        sys.exit(f"contentgun MCP server: {exc}")
    if config.smtp is None:
        logger.warning(
            "MCP_MAIL=console: sign-in codes go to this log instead of a mailbox. "
            "Use it for development only."
        )
    logger.info("Serving %s, rendering through %s", config.public_url, config.base_url)
    uvicorn.run(
        app,
        host=arguments.host,
        port=arguments.port,
        log_level="info",
        # The access log would show query strings, and the sign-in carries its id in one.
        access_log=False,
        # Claude holds one request open for minutes; a deploy must not wait for it.
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
        # Longer than the two minutes Caddy keeps an idle upstream connection, so Caddy never
        # sends a request into a connection uvicorn has just closed.
        timeout_keep_alive=KEEP_ALIVE_S,
        server_header=False,
    )


if __name__ == "__main__":
    main()
