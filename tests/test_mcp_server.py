import os
import socket
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from mcp import Client, StdioServerParameters
from PIL import Image

from app.schemas import MAX_FIELD_LENGTH, RenderRequest
from client import AssetsClient
from config import Config
from server import LIMITS, build_server
from tools import Tools

SERVER = Path(__file__).resolve().parents[1] / "mcp"
KEY = "client-secret-key"
TEMPLATES = [
    {"id": "brand/card", "bundle": "brand", "name": "card"},
    {"id": "brand/carousel-cover", "bundle": "brand", "name": "carousel-cover"},
]
TOOLS = {"list_templates", "render_card", "render_carousel"}


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (108, 135), (0, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


def service(request: httpx.Request) -> httpx.Response:
    if request.method == "GET":
        return httpx.Response(200, json={"ok": True, "templates": TEMPLATES})
    return httpx.Response(
        200, content=png_bytes(), headers={"Content-Type": "image/png", "X-Request-Id": "req-1"}
    )


def unauthorized(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"ok": False, "error": "Unauthorized."})


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / "Social"
    folder.mkdir()
    return folder.resolve()


def server_for(root: Path, handler=service):
    config = Config(KEY, "http://assets.test", 5, root, (root,), "brand")
    client = AssetsClient(config, transport=httpx.MockTransport(handler))
    return build_server(Tools(config, client))


async def listed_tools(root: Path) -> dict:
    async with Client(server_for(root)) as client:
        listed = await client.list_tools()
    return {tool.name: tool for tool in listed.tools}


async def call(root: Path, name: str, arguments: dict, handler=service):
    async with Client(server_for(root, handler)) as client:
        return await client.call_tool(name, arguments)


def typed_limit(field: str) -> int:
    for item in RenderRequest.model_fields[field].metadata:
        if hasattr(item, "max_length"):
            return item.max_length
    raise AssertionError(f"{field} declares no maximum length")


@pytest.mark.parametrize(
    "field", ["headline", "subline", "handle", "topic", "pagination", "body", "cta"]
)
def test_the_limits_in_the_descriptions_match_the_service(field):
    expected = typed_limit(field) if field in RenderRequest.model_fields else MAX_FIELD_LENGTH
    clause = next(part for part in LIMITS.split(";") if field in part)
    assert str(expected) in clause


async def test_three_tools_are_offered_with_their_hints(root):
    tools = await listed_tools(root)
    assert set(tools) == TOOLS
    assert tools["list_templates"].annotations.read_only_hint is True
    for name in ("render_card", "render_carousel"):
        assert tools[name].annotations.read_only_hint is False
        assert tools[name].annotations.destructive_hint is False
    assert not tools["list_templates"].input_schema.get("required")
    assert set(tools["render_card"].input_schema["required"]) == {"output_path"}
    assert {"pdf", "bundle"} <= set(tools["render_carousel"].input_schema["properties"])
    assert {"image_path", "icon_path"} <= set(tools["render_card"].input_schema["properties"])
    carousel = tools["render_carousel"].input_schema
    slide = carousel["$defs"]["CarouselSlide"]["properties"]
    assert {"image_path", "icon_path"} <= set(slide)
    colours = carousel["properties"]["icon_color"]["anyOf"][0]["enum"]
    assert colours == ["accent", "text"]
    assert set(tools["render_carousel"].input_schema["required"]) == {
        "output_dir",
        "cover",
        "slides",
        "closing",
    }


async def test_the_path_parameters_name_the_output_folder(root):
    tools = await listed_tools(root)
    card = tools["render_card"].input_schema["properties"]["output_path"]["description"]
    carousel = tools["render_carousel"].input_schema["properties"]["output_dir"]["description"]
    assert str(root) in card
    assert str(root) in carousel


async def test_list_templates_returns_structured_content(root):
    result = await call(root, "list_templates", {})
    assert result.is_error is False
    assert result.structured_content == {"templates": TEMPLATES}


async def test_render_card_returns_the_path_and_a_jpeg_preview(root):
    result = await call(root, "render_card", {"output_path": "card.png", "headline": "Hallo"})
    assert result.is_error is False
    text, image = result.content
    assert str(root / "card.png") in text.text
    assert image.type == "image"
    assert image.mime_type == "image/jpeg"
    assert (root / "card.png").read_bytes() == png_bytes()


async def test_render_carousel_takes_the_slides_as_objects(root):
    arguments = {
        "output_dir": "carousel",
        "cover": {
            "headline": "Drei Fehler",
            "topic": "Automatisierung",
            "subline": "Und wie du sie vermeidest.",
        },
        "slides": [{"headline": "Kein Ziel", "body": "Leg vorher fest, woran du Erfolg erkennst."}],
        "closing": {"headline": "Fazit", "cta": "Welcher Fehler passiert dir?"},
        "handle": "@contentgun",
    }
    result = await call(root, "render_carousel", arguments)
    assert result.is_error is False
    assert sorted(path.name for path in (root / "carousel").iterdir()) == [
        "01.png",
        "02.png",
        "03.png",
    ]
    assert [block.type for block in result.content] == ["text", "image"]


async def test_a_service_failure_is_an_error_result(root):
    result = await call(root, "list_templates", {}, handler=unauthorized)
    assert result.is_error is True
    assert "rejected ASSETS_API_KEY" in result.content[0].text


async def test_a_path_outside_the_folder_is_an_error_result(root):
    result = await call(root, "render_card", {"output_path": "../escape.png", "headline": "Hallo"})
    assert result.is_error is True
    assert "outside the output folder" in result.content[0].text


async def test_a_carousel_without_content_slides_is_rejected(root):
    arguments = {
        "output_dir": "carousel",
        "cover": {"headline": "Hook"},
        "slides": [],
        "closing": {"headline": "Fazit"},
    }
    result = await call(root, "render_carousel", arguments)
    assert result.is_error is True
    assert not (root / "carousel").exists()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_the_desktop_app_launch_works_from_any_folder(tmp_path, root):
    # Like the desktop app: the folder run directly, from a foreign working directory.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER)],
        env={
            "ASSETS_API_KEY": KEY,
            "ASSETS_BASE_URL": f"http://127.0.0.1:{free_port()}",
            "ASSETS_OUTPUT_ROOT": str(root),
        },
        cwd=elsewhere,
    )
    async with Client(parameters, read_timeout_seconds=30) as client:
        listed = await client.list_tools()
        result = await client.call_tool("list_templates", {})
    assert {tool.name for tool in listed.tools} == TOOLS
    assert result.is_error is True
    assert "Could not reach" in result.content[0].text


def test_startup_without_a_key_names_the_variable(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(SERVER)],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 1
    assert "ASSETS_API_KEY" in completed.stderr
    assert completed.stdout == ""
