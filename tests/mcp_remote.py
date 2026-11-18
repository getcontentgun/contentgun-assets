"""The remote MCP server in process, and the steps claude.ai takes against it: register, authorize,
sign in with the code from the mail, exchange and refresh tokens, call tools over JSON-RPC."""

import base64
import hashlib
import os
import re
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from asgi_lifespan import LifespanManager

from remote_config import CLAUDE_CALLBACKS, RemoteConfig
from remote_server import build_app
from tests.conftest import write_tenants_file

PUBLIC = "https://mcp.test"
RESOURCE = f"{PUBLIC}/mcp"
CALLBACK = CLAUDE_CALLBACKS[0]
SECRET = "secret-of-the-remote-tests-0123456789abcdef"
INTERNAL_KEY = "internal-key-of-the-test-suite-0123456789"
ADDRESS = "team@acme.example"
MODERN = "2026-07-28"
LEGACY = "2025-11-25"
STATE = "state-of-claude"
CLAUDE_REGISTRATION = {
    "client_name": "Claude",
    "redirect_uris": [CALLBACK],
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "client_secret_post",
    "scope": "cards offline_access",
    "application_type": "web",
}
CODE_IN_MAIL = re.compile(r"(\d{4}) (\d{4})")


def remote_config(**changes: Any) -> RemoteConfig:
    values: dict[str, Any] = {
        "public_url": PUBLIC,
        "secret": SECRET,
        "base_url": "http://assets.test",
        "api_key": INTERNAL_KEY,
        "timeout_s": 5.0,
        "tenants_file": None,
        "emails": (ADDRESS,),
        "smtp": None,
        "mail_from": "Contentgun <login@contentgun.test>",
        "sessions_after": None,
        "access_minutes": 60,
        "redirect_uris": CLAUDE_CALLBACKS,
        "imprint_url": None,
        "privacy_url": None,
    }
    return RemoteConfig(**(values | changes))


def rewrite_tenants(path: Path, tenants: dict[str, dict[str, Any]]) -> Path:
    """Writes the tenants file, and moves its time on when it existed, as a later edit would."""
    stamp = path.stat().st_mtime_ns if path.exists() else None
    write_tenants_file(path, tenants)
    if stamp is not None:
        os.utime(path, ns=(stamp + 2_000_000_000, stamp + 2_000_000_000))
    return path


class Clock:
    """Starts at the real time and only moves forward, so the SDK's own checks against the real
    time agree with every token the server signs."""

    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Outbox:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []
        self.failing = False

    async def send(self, message: EmailMessage) -> None:
        if self.failing:
            raise ConnectionRefusedError("refused for team@acme.example")
        self.messages.append(message)

    def code(self, index: int = -1) -> str:
        match = CODE_IN_MAIL.search(self.messages[index].get_content())
        assert match is not None, "The mail carries no code."
        return match.group(1) + match.group(2)


@dataclass
class Remote:
    client: httpx.AsyncClient
    outbox: Outbox
    clock: Clock
    config: RemoteConfig


@asynccontextmanager
async def running(
    config: RemoteConfig | None = None,
    *,
    clock: Clock | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[Remote]:
    """The remote server in process; transport stands in for the assets service."""
    config = config or remote_config()
    clock = clock or Clock()
    outbox = Outbox()
    app = build_app(config, mailer=outbox, clock=clock, transport=transport)
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url=config.public_url) as client:
            yield Remote(client, outbox, clock, config)


async def register(client: httpx.AsyncClient, **changes: Any) -> httpx.Response:
    return await client.post("/register", json=CLAUDE_REGISTRATION | changes)


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def authorize(
    client: httpx.AsyncClient, client_id: str, challenge: str, **changes: str | None
) -> httpx.Response:
    """GET /authorize as Claude sends it; a change to None leaves the parameter out."""
    params: dict[str, str | None] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": CALLBACK,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": STATE,
        "scope": "cards offline_access",
        "resource": RESOURCE,
        "prompt": "consent",
    }
    sent = {name: value for name, value in (params | changes).items() if value is not None}
    return await client.get("/authorize", params=sent)


def txn_of(response: httpx.Response) -> str:
    assert response.status_code == 302, response.text
    target = urlsplit(response.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == f"{PUBLIC}/login"
    return parse_qs(target.query)["txn"][0]


async def start(remote: Remote) -> tuple[dict[str, Any], str, str]:
    """Registers like Claude and starts a sign-in: the client, the verifier and the txn."""
    registered = (await register(remote.client)).json()
    verifier, challenge = pkce()
    txn = txn_of(await authorize(remote.client, registered["client_id"], challenge))
    return registered, verifier, txn


async def submit_address(
    client: httpx.AsyncClient, txn: str, address: str = ADDRESS, **headers: str
) -> httpx.Response:
    return await client.post("/login", data={"txn": txn, "email": address}, headers=headers)


async def submit_code(
    client: httpx.AsyncClient, txn: str, code: str, **headers: str
) -> httpx.Response:
    return await client.post("/login/code", data={"txn": txn, "code": code}, headers=headers)


def callback_of(response: httpx.Response) -> dict[str, str]:
    assert response.status_code == 303, response.text
    target = urlsplit(response.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == CALLBACK
    return {name: values[0] for name, values in parse_qs(target.query).items()}


async def exchange(
    client: httpx.AsyncClient, registered: dict[str, Any], code: str, verifier: str
) -> httpx.Response:
    return await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CALLBACK,
            "client_id": registered["client_id"],
            "client_secret": registered["client_secret"],
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
    )


async def refresh(
    client: httpx.AsyncClient, registered: dict[str, Any], token: str, **changes: str
) -> httpx.Response:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": token,
        "client_id": registered["client_id"],
        "client_secret": registered["client_secret"],
    }
    return await client.post("/token", data=data | changes)


@dataclass
class SignedIn:
    registered: dict[str, Any]
    tokens: dict[str, Any]

    @property
    def access(self) -> str:
        return self.tokens["access_token"]


async def sign_in(remote: Remote, address: str = ADDRESS) -> SignedIn:
    registered, verifier, txn = await start(remote)
    assert (await submit_address(remote.client, txn, address)).status_code == 200
    callback = callback_of(await submit_code(remote.client, txn, remote.outbox.code()))
    response = await exchange(remote.client, registered, callback["code"], verifier)
    assert response.status_code == 200, response.text
    return SignedIn(registered, response.json())


async def call(
    client: httpx.AsyncClient,
    token: str | None,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    protocol: str = MODERN,
) -> httpx.Response:
    """One JSON-RPC request as Claude sends it: the 2026-07-28 envelope of the chat, or the
    older protocol of Anthropic's connector check."""
    params = dict(params or {})
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if protocol == MODERN:
        params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": MODERN,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "tests", "version": "0"},
        }
        headers |= {"MCP-Protocol-Version": MODERN, "Mcp-Method": method}
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
    elif method != "initialize":
        headers["MCP-Protocol-Version"] = protocol
    message = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    return await client.post("/mcp", json=message, headers=headers)


async def tool(
    client: httpx.AsyncClient,
    token: str,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    protocol: str = MODERN,
) -> dict[str, Any]:
    response = await call(
        client, token, "tools/call", {"name": name, "arguments": arguments or {}}, protocol=protocol
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]
