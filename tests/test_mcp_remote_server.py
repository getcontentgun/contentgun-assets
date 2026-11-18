import ast
import logging
import sys
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

import app
from tests.mcp_remote import (
    ADDRESS,
    LEGACY,
    PUBLIC,
    RESOURCE,
    call,
    remote_config,
    running,
    sign_in,
    tool,
)
from version import __version__

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"
LOCAL_ONLY = {"paths", "tools", "server", "config", "__main__"}
REMOTE = {
    "accounts",
    "login",
    "login_pages",
    "mailer",
    "oauth",
    "remote_config",
    "remote_server",
    "signing",
    "version",
}


async def test_claude_signs_in_with_the_code_and_calls_a_tool():
    async with running() as remote:
        signed_in = await sign_in(remote)
        result = await tool(remote.client, signed_in.access, "whoami")
    assert result["isError"] is False
    assert result["structuredContent"]["email"] == ADDRESS
    assert result["structuredContent"]["tenant"] == "default"
    assert result["structuredContent"]["access_expires_at"].endswith("Z")


async def test_the_older_protocol_of_the_connector_check_works_without_a_session():
    async with running() as remote:
        token = (await sign_in(remote)).access
        opened = await call(
            remote.client,
            token,
            "initialize",
            {
                "protocolVersion": LEGACY,
                "capabilities": {},
                "clientInfo": {"name": "Anthropic/ClaudeAI", "version": "1.0.0"},
            },
            protocol=LEGACY,
        )
        listed = await call(remote.client, token, "tools/list", protocol=LEGACY)
        result = await tool(remote.client, token, "whoami", protocol=LEGACY)
    assert opened.status_code == 200
    assert "mcp-session-id" not in opened.headers
    assert [entry["name"] for entry in listed.json()["result"]["tools"]] == ["whoami"]
    assert result["structuredContent"]["email"] == ADDRESS


async def test_without_a_token_mcp_points_to_the_resource_metadata():
    async with running() as remote:
        response = await call(remote.client, None, "tools/list")
        metadata = await remote.client.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 401
    assert (
        f'resource_metadata="{PUBLIC}/.well-known/oauth-protected-resource/mcp"'
        in response.headers["www-authenticate"]
    )
    assert metadata.json()["resource"] == RESOURCE
    assert metadata.json()["authorization_servers"] == [PUBLIC]


async def test_the_metadata_offers_secrets_only_and_no_revocation():
    async with running() as remote:
        metadata = (await remote.client.get("/.well-known/oauth-authorization-server")).json()
    assert metadata["issuer"] == PUBLIC
    assert metadata["registration_endpoint"] == f"{PUBLIC}/register"
    assert "none" not in metadata["token_endpoint_auth_methods_supported"]
    assert "revocation_endpoint" not in metadata
    assert metadata["scopes_supported"] == ["cards", "offline_access"]
    assert metadata["code_challenge_methods_supported"] == ["S256"]


async def test_a_bad_bearer_value_is_a_401_and_never_a_500():
    values = ["x", "a.b", "é" * 50, "a" * 5000 + ".b", "eyJ9." + "A" * 43, "e30." + "A" * 43]
    async with running() as remote:
        for value in values:
            authorization = b"Bearer " + value.encode("utf-8")
            response = await remote.client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={
                    "Authorization": authorization,
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": LEGACY,
                },
            )
            assert response.status_code == 401, value
            metadata = await remote.client.get(
                "/.well-known/oauth-authorization-server",
                headers={"Authorization": authorization},
            )
            assert metadata.status_code == 200, value


async def test_mcp_takes_its_own_host_with_and_without_the_default_port_only():
    async with running() as remote:
        token = (await sign_in(remote)).access
        plain = await call(remote.client, token, "tools/list")
        remote.client.headers["Host"] = "mcp.test:443"
        with_port = await call(remote.client, token, "tools/list")
        remote.client.headers["Host"] = "evil.example"
        foreign = await call(remote.client, token, "tools/list")
    assert (plain.status_code, with_port.status_code, foreign.status_code) == (200, 200, 421)


async def test_a_foreign_origin_is_refused_and_claude_s_is_taken():
    async with running() as remote:
        token = (await sign_in(remote)).access
        claude = await remote.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": LEGACY,
                "Origin": "https://claude.ai",
            },
        )
        foreign = await remote.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": LEGACY,
                "Origin": "https://evil.example",
            },
        )
    assert claude.status_code == 200
    assert foreign.status_code == 403


async def test_health_answers_without_a_token():
    async with running() as remote:
        response = await remote.client.get("/health")
    assert response.json() == {"ok": True, "service": "contentgun-mcp", "version": __version__}


async def test_a_whole_sign_in_leaves_no_secret_in_the_log(caplog):
    caplog.set_level(logging.DEBUG)
    async with running() as remote:
        signed_in = await sign_in(remote)
        await tool(remote.client, signed_in.access, "whoami")
        code = remote.outbox.code()
    secrets_seen = [
        signed_in.access,
        signed_in.tokens["refresh_token"],
        signed_in.registered["client_id"],
        signed_in.registered["client_secret"],
        code,
        f"{code[:4]} {code[4:]}",
        ADDRESS,
        remote_config().secret,
    ]
    for record in caplog.records:
        line = record.getMessage()
        for value in secrets_seen:
            assert value not in line, f"{record.name} logged a secret: {line}"
    assert any("t…@acme.example" in record.getMessage() for record in caplog.records)


def test_the_version_equals_the_one_of_the_service():
    assert __version__ == app.__version__


def test_the_folder_stays_a_plain_folder():
    assert not (MCP_DIR / "__init__.py").exists()


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("path", sorted(MCP_DIR.glob("*.py")), ids=lambda path: path.name)
def test_nothing_in_the_folder_imports_the_service_or_the_tests(path):
    assert not _imports(path) & {"app", "tests"}


@pytest.mark.parametrize("name", sorted(REMOTE))
def test_the_remote_server_imports_nothing_of_the_local_one(name):
    assert not _imports(MCP_DIR / f"{name}.py") & LOCAL_ONLY


@pytest.mark.parametrize("path", sorted(MCP_DIR.glob("*.py")), ids=lambda path: path.name)
def test_no_module_of_the_folder_shadows_the_standard_library_or_a_package(path):
    name = path.stem
    assert name not in sys.stdlib_module_names
    assert name == "mcp" or name not in packages_distributions()
