import re
from pathlib import Path

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
