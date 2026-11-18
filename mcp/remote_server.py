import argparse
import logging
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from accounts import Accounts
from errors import ConfigError
from login import Logins
from login_pages import add_login_routes
from mailer import Mailer, mailer_for
from oauth import REQUIRED_SCOPE, SCOPES, Provider
from remote_config import RemoteConfig, load_remote_config
from version import __version__

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8020
CLAUDE_ORIGINS = ("https://claude.ai", "https://claude.com")
GRACEFUL_SHUTDOWN_S = 3
KEEP_ALIVE_S = 130
QUIET_LOGGERS = ("httpx", "mcp")

INSTRUCTIONS = """\
Contentgun renders social media cards and carousels with the templates of the signed-in team. \
whoami shows which address and team are signed in."""

WHOAMI = ToolAnnotations(read_only_hint=True, open_world_hint=False)


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


def _add_tools(server: MCPServer) -> None:
    @server.tool(
        description="Show which address and team are signed in to Contentgun, and until when.",
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


def build_app(
    config: RemoteConfig,
    *,
    mailer: Mailer | None = None,
    clock: Callable[[], float] = time.time,
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
        title="Contentgun",
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
    _add_tools(server)
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
    parser = argparse.ArgumentParser(description="The remote MCP server of Contentgun.")
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
