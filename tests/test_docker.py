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
