import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "docker" / "assets.Dockerfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "compose.dev.yaml").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8")


def test_the_base_image_matches_the_playwright_pin():
    # The browsers in the image belong to one Playwright version; the Python package must be it.
    pin = re.search(r"^playwright==(\S+)$", (ROOT / "requirements.txt").read_text(), re.M)
    image = re.search(
        r"^FROM mcr\.microsoft\.com/playwright/python:v(\S+)-noble$", DOCKERFILE, re.M
    )
    assert pin is not None
    assert image is not None
    assert image.group(1) == pin.group(1)


def test_the_image_carries_the_built_in_bundles():
    assert re.search(r"^WORKDIR /app$", DOCKERFILE, re.M)
    assert re.search(r"^COPY bundles/ bundles/$", DOCKERFILE, re.M)
    assert re.search(r"^!bundles/$", DOCKERIGNORE, re.M)
    assert "BUNDLES_DIR" not in DOCKERFILE


def test_compose_mounts_the_built_in_bundles_over_the_copy_in_the_image():
    assert re.search(r'^\s+- "?\./bundles:/app/bundles:ro"?$', COMPOSE, re.M)


def test_compose_blanks_the_paths_that_env_file_would_pass_on():
    for variable in ("BUNDLES_DIR", "TENANTS_FILE"):
        assert re.search(rf'^\s+{variable}: ""$', COMPOSE, re.M), variable


def test_compose_publishes_the_image_port_on_localhost_only():
    port = re.search(r'"--port", "(\d+)"', DOCKERFILE)
    assert port is not None
    mapping = rf'^\s+- "?127\.0\.0\.1:\$\{{PORT:-\d+\}}:{port.group(1)}"?$'
    assert re.search(mapping, COMPOSE, re.M)


def test_the_service_runs_as_the_unprivileged_user():
    assert re.search(r"^USER pwuser$", DOCKERFILE, re.M)


def service(name: str) -> str:
    """The block of one service in compose.dev.yaml, up to the next one."""
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \S|^\S|\Z)", COMPOSE, re.M | re.S)
    assert match is not None, name
    return match.group(1)


def test_the_storage_starts_only_with_its_profile():
    assert re.search(r'^\s+profiles: \["storage"\]$', service("storage"), re.M)
    assert "profiles" not in service("assets")


def test_the_storage_is_pinned_and_published_on_localhost_only():
    block = service("storage")
    assert re.search(r"^\s+image: rustfs/rustfs:\d+\.\d+\.\d+$", block, re.M)
    ports = re.findall(r'^\s+- "?([^"\n]+)"?$', block.split("environment:")[0], re.M)
    assert ports and all(port.startswith("127.0.0.1:") for port in ports)


def storage_host_port(variable: str, inside: int) -> str:
    mapping = rf'^\s+- "127\.0\.0\.1:\$\{{{variable}:-(\d+)\}}:{inside}"$'
    match = re.search(mapping, service("storage"), re.M)
    assert match is not None, variable
    return match.group(1)


def test_the_storage_ports_of_the_host_can_be_moved():
    assert storage_host_port("STORAGE_PORT", 9000)
    assert storage_host_port("STORAGE_CONSOLE_PORT", 9001)


def test_the_example_endpoint_names_the_storage_port_of_the_host():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    port = storage_host_port("STORAGE_PORT", 9000)
    assert f"# S3_ENDPOINT=http://127.0.0.1:{port}\n" in example


def test_the_container_reaches_the_storage_by_its_service_name():
    # .env names the storage as the host sees it, and env_file would pass that on unchanged.
    assert re.search(
        r"^\s+S3_ENDPOINT: \$\{S3_ENDPOINT_IN_COMPOSE:-\$\{S3_ENDPOINT:-\}\}$",
        service("assets"),
        re.M,
    )


MCP_DOCKERFILE = (ROOT / "docker" / "mcp.Dockerfile").read_text(encoding="utf-8")
CADDYFILE = (ROOT / "docker" / "gateway.Caddyfile").read_text(encoding="utf-8")
DOCKERFILES = {"assets": DOCKERFILE, "mcp": MCP_DOCKERFILE}


def test_the_mcp_image_is_pinned_by_version_and_digest():
    base = re.search(
        r"^FROM python:3\.(\d+)\.\d+-slim-[a-z]+@sha256:[0-9a-f]{64}$", MCP_DOCKERFILE, re.M
    )
    assert base is not None
    assert int(base.group(1)) >= 11


def test_the_mcp_image_holds_the_mcp_folder_and_its_requirements_only():
    copied = re.findall(r"^COPY (\S+) ", MCP_DOCKERFILE, re.M)
    assert copied == ["requirements-mcp.txt", "mcp/"]
    assert re.search(r"^RUN python -m pip install -r requirements-mcp\.txt$", MCP_DOCKERFILE, re.M)


def test_the_mcp_image_runs_as_a_user_of_its_own():
    user = re.search(r"^USER (\S+)$", MCP_DOCKERFILE, re.M)
    assert user is not None
    assert user.group(1) not in ("root", "0")


def test_the_mcp_image_starts_the_remote_server_as_a_script():
    # Started as a script, mcp/ comes first on the import path; the default port of the server
    # is the one the image publishes.
    from remote_server import DEFAULT_PORT

    start = re.search(
        r'^CMD \["python", "mcp/remote_server\.py", "--host", "0\.0\.0\.0", "--port", "(\d+)"\]$',
        MCP_DOCKERFILE,
        re.M,
    )
    assert start is not None
    port = start.group(1)
    assert port == str(DEFAULT_PORT)
    assert re.search(rf"^EXPOSE {port}$", MCP_DOCKERFILE, re.M)
    assert f"http://127.0.0.1:{port}/health" in MCP_DOCKERFILE


@pytest.mark.parametrize("name", sorted(DOCKERFILES))
def test_the_build_sees_every_file_a_dockerfile_copies(name):
    lines = DOCKERIGNORE.splitlines()
    for source in re.findall(r"^COPY (\S+) ", DOCKERFILES[name], re.M):
        assert f"!{source}" in lines, source
        assert lines.index(f"!{source}") < lines.index("**/__pycache__"), source


def test_mcp_and_gateway_start_only_with_the_profile_remote():
    for name in ("mcp", "gateway"):
        assert re.search(r'^\s+profiles: \["remote"\]$', service(name), re.M), name


def test_the_mcp_service_gets_named_settings_and_not_the_whole_env_file():
    block = service("mcp")
    assert not re.search(r"^\s+env_file:", block, re.M)
    assert not re.search(r"^\s+(API_KEYS|S3_\w+):", block, re.M)
    port = re.search(r'"--port", "(\d+)"', DOCKERFILE)
    assert re.search(rf"^\s+ASSETS_BASE_URL: http://assets:{port.group(1)}$", block, re.M)


def test_no_variable_of_compose_is_required():
    # Compose resolves the whole file before it looks at profiles, so one required variable of
    # a profile would stop every plain start.
    assert ":?" not in COMPOSE


def test_the_gateway_is_pinned_and_published_on_localhost_only():
    block = service("gateway")
    assert re.search(r"^\s+image: caddy:\d+\.\d+\.\d+(-alpine)?@sha256:[0-9a-f]{64}$", block, re.M)
    assert re.search(r'^\s+- "127\.0\.0\.1:\$\{GATEWAY_PORT:-\d+\}:8080"$', block, re.M)
    assert re.search(r"^:8080 \{$", CADDYFILE, re.M)


def test_the_gateway_sends_the_file_urls_to_the_service_and_the_rest_to_mcp():
    service_port = re.search(r'"--port", "(\d+)"', DOCKERFILE).group(1)
    mcp_port = re.search(r'"--port", "(\d+)"', MCP_DOCKERFILE).group(1)
    assert re.search(r"^\s+@files path /o/\* /u/\*$", CADDYFILE, re.M)
    assert re.search(rf"^\s+reverse_proxy assets:{service_port}$", CADDYFILE, re.M)
    assert re.search(rf"^\s+reverse_proxy mcp:{mcp_port} \{{$", CADDYFILE, re.M)
    assert "/v1" not in CADDYFILE


def test_both_images_name_their_source():
    for name, text in DOCKERFILES.items():
        assert re.search(
            r'^LABEL org\.opencontainers\.image\.source="https://github\.com/getcontentgun/'
            r'contentgun-assets"$',
            text,
            re.M,
        ), name
