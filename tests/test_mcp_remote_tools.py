import base64
import json
import logging
import re
from contextlib import asynccontextmanager
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.schemas import RESERVED_KEYS
from cards import LIMITS
from remote_server import RENDER_CARD, RENDER_CAROUSEL
from remote_tools import NO_PREVIEW, REMOTE, RESERVED
from tests.mcp_remote import (
    ADDRESS,
    INTERNAL_KEY,
    call,
    remote_config,
    rewrite_tenants,
    running,
    sign_in,
    tool,
)

STORED = "https://files.test/o/default/" + "A" * 32 + "/"
UPLOAD_URL = "https://files.test/u/default.up_abcdefghijklmnopqrstuvwx.1795000000.signature"
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M4 12l5 5L20 6"/></svg>'
CAROUSEL = {
    "cover": {"headline": "Drei Dinge", "topic": "Thema"},
    "slides": [
        {"headline": "Eins", "body": "Der erste Punkt.", "icon_svg": SVG},
        {"headline": "Zwei", "icon_url": "https://example.com/icon.svg"},
    ],
    "closing": {"headline": "Fertig", "cta": "Und du?"},
}


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (108, 135), (30, 42, 54)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG = png_bytes()


class FakeService:
    """The assets service as the remote server sees it, with every request recorded."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []
        self.documents: list[dict] = []
        self.templates = [
            {"id": f"demo/{name}"}
            for name in (
                "card",
                "card-image",
                "carousel-cover",
                "carousel-content",
                "carousel-closing",
            )
        ]
        self.answers: dict[str, httpx.Response] = {}
        self.fail_render: int | None = None
        self.unreachable = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.unreachable:
            raise httpx.ConnectError("Connection refused", request=request)
        path = request.url.path
        if path in self.answers:
            return self.answers[path]
        if path == "/v1/templates":
            return httpx.Response(200, json={"ok": True, "templates": self.templates})
        if path == "/v1/uploads":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "upload_id": "up_abcdefghijklmnopqrstuvwx",
                    "upload_url": UPLOAD_URL,
                    "expires_at": "2026-11-18T12:15:00Z",
                    "max_bytes": 8_388_608,
                },
            )
        if path == "/v1/render":
            self.bodies.append(json.loads(request.content))
            number = len(self.bodies)
            if number == self.fail_render:
                return httpx.Response(400, json={"ok": False, "error": "Field 'x' is wrong."})
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "url": f"{STORED}{number:02d}.png",
                    "content_type": "image/png",
                    "width": 1080,
                    "height": 1350,
                    "bytes": len(PNG),
                    "expires_at": f"2026-11-19T12:00:{number:02d}Z",
                },
                headers={"x-request-id": f"request-{number}"},
            )
        if path == "/v1/document":
            body = json.loads(request.content)
            self.documents.append(body)
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "url": f"{STORED}document.pdf",
                    "content_type": "application/pdf",
                    "width": 1080,
                    "height": 1350,
                    "bytes": 2048,
                    "expires_at": "2026-11-19T12:01:00Z",
                    "pages": len(body["slides"]),
                },
            )
        if path.startswith("/o/"):
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        return httpx.Response(404, json={"ok": False, "error": "Not found."})

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


@asynccontextmanager
async def connected(service: FakeService, config=None):
    async with running(config, transport=httpx.MockTransport(service)) as remote:
        token = (await sign_in(remote)).access
        yield remote, token


def text_of(result: dict) -> str:
    """The text of a result; for an error without the prefix the SDK puts before it."""
    text = result["content"][0]["text"]
    return re.sub(r"^Error executing tool \w+: ", "", text) if result["isError"] else text


async def test_every_call_carries_the_internal_key_and_the_tenant_of_the_token():
    service = FakeService()
    async with connected(service) as (remote, token):
        result = await tool(remote.client, token, "list_templates")
    [request] = service.requests
    assert request.headers["authorization"] == f"Bearer {INTERNAL_KEY}"
    assert request.headers["x-contentgun-tenant"] == "default"
    assert request.url.host == "assets.test"
    assert result["structuredContent"] == {"templates": service.templates}


async def test_the_tenant_comes_from_the_tenants_file(tmp_path):
    service = FakeService()
    path = rewrite_tenants(tmp_path / "tenants.toml", {"acme": {"emails": [ADDRESS]}})
    async with connected(service, remote_config(tenants_file=path, emails=())) as (remote, token):
        await tool(remote.client, token, "list_templates")
    assert service.requests[0].headers["x-contentgun-tenant"] == "acme"


async def test_no_tool_takes_a_tenant():
    async with connected(FakeService()) as (remote, token):
        listed = (await call(remote.client, token, "tools/list")).json()["result"]["tools"]
    for entry in listed:
        assert "tenant" not in json.dumps(entry["inputSchema"])


async def test_render_card_stores_the_card_and_answers_with_its_url_and_a_preview():
    service = FakeService()
    async with connected(service) as (remote, token):
        result = await tool(
            remote.client,
            token,
            "render_card",
            {"headline": "Hallo", "template": "demo/card", "handle": "@contentgun"},
        )
    assert result["isError"] is False
    assert service.bodies == [
        {"template": "demo/card", "headline": "Hallo", "handle": "@contentgun", "output": "url"}
    ]
    text, image = result["content"]
    assert f"(1080x1350, 0 KB): {STORED}01.png" in text["text"]
    assert "The URL works until 2026-11-19T12:00:01Z." in text["text"]
    assert image["mimeType"] == "image/jpeg"
    assert Image.open(BytesIO(base64.b64decode(image["data"]))).width == 540
    fetch = service.requests[-1]
    assert fetch.url.path == f"/o/default/{'A' * 32}/01.png"
    assert fetch.url.host == "assets.test"
    assert "authorization" not in fetch.headers
    assert "x-contentgun-tenant" not in fetch.headers


@pytest.mark.parametrize(
    ("argument", "value", "key", "sent"),
    [
        (
            "image_url",
            "https://example.com/photo.jpg",
            "image_url",
            "https://example.com/photo.jpg",
        ),
        ("image_upload", "up_abc", "image_upload", "up_abc"),
        ("icon_url", "https://example.com/icon.svg", "icon_url", "https://example.com/icon.svg"),
        ("icon_upload", "up_def", "icon_upload", "up_def"),
        ("icon_svg", SVG, "icon", base64.b64encode(SVG.encode()).decode()),
    ],
)
async def test_each_image_or_icon_source_lands_in_the_body(argument, value, key, sent):
    service = FakeService()
    async with connected(service) as (remote, token):
        await tool(remote.client, token, "render_card", {"headline": "Hallo", argument: value})
    assert service.bodies[0][key] == sent


async def test_two_sources_are_refused_before_any_request():
    service = FakeService()
    async with connected(service) as (remote, token):
        result = await tool(
            remote.client,
            token,
            "render_card",
            {"headline": "Hallo", "image_url": "https://example.com/a.png", "icon_svg": SVG},
        )
    assert result["isError"] is True
    assert text_of(result) == "Pass one image or icon, not image_url and icon_svg together."
    assert service.requests == []


def test_the_reserved_keys_cover_those_of_the_service():
    assert set(RESERVED) >= RESERVED_KEYS | {"headline", "subline", "handle"}


@pytest.mark.parametrize("key", sorted(RESERVED_KEYS))
async def test_extra_fields_cannot_set_a_reserved_key(key):
    service = FakeService()
    async with connected(service) as (remote, token):
        result = await tool(
            remote.client, token, "render_card", {"headline": "Hallo", "extra_fields": {key: "x"}}
        )
    assert result["isError"] is True
    assert text_of(result).startswith(f"extra_fields must not contain {key!r}.")
    assert service.requests == []


async def test_further_fields_reach_the_body():
    service = FakeService()
    async with connected(service) as (remote, token):
        await tool(
            remote.client,
            token,
            "render_card",
            {"headline": "Hallo", "extra_fields": {"body": "Text", "icon_color": "text"}},
        )
    assert service.bodies[0]["body"] == "Text"
    assert service.bodies[0]["icon_color"] == "text"


async def test_without_the_stored_file_the_url_still_comes_back():
    service = FakeService()
    service.answers[f"/o/default/{'A' * 32}/01.png"] = httpx.Response(404, json={"ok": False})
    async with connected(service) as (remote, token):
        result = await tool(remote.client, token, "render_card", {"headline": "Hallo"})
    assert result["isError"] is False
    [text] = result["content"]
    assert f"{STORED}01.png" in text["text"]
    assert NO_PREVIEW in text["text"]


async def test_a_message_of_the_service_becomes_the_tool_error():
    service = FakeService()
    service.answers["/v1/render"] = httpx.Response(
        400, json={"ok": False, "error": "Unknown template 'x/y'."}
    )
    async with connected(service) as (remote, token):
        result = await tool(remote.client, token, "render_card", {"headline": "Hallo"})
    assert result["isError"] is True
    assert text_of(result) == "Unknown template 'x/y'. Retrying the same request fails again."


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (httpx.Response(401, json={"ok": False, "error": "Unauthorized."}), REMOTE.rejected),
        (
            httpx.Response(500, text="boom"),
            "The ContentGun service answered unexpectedly, with HTTP 500.",
        ),
        (None, REMOTE.unreachable),
    ],
)
async def test_a_failure_names_no_internal_address(answer, expected):
    service = FakeService()
    async with connected(service) as (remote, token):
        if answer is None:
            service.unreachable = True
        else:
            service.answers["/v1/templates"] = answer
        result = await tool(remote.client, token, "list_templates")
    assert result["isError"] is True
    assert text_of(result) == expected
    assert "assets.test" not in text_of(result)
    assert "ASSETS_API_KEY" not in text_of(result)


async def test_create_upload_returns_the_url_and_the_command_to_run():
    service = FakeService()
    async with connected(service) as (remote, token):
        result = await tool(remote.client, token, "create_upload")
    assert result["structuredContent"] == {
        "upload_id": "up_abcdefghijklmnopqrstuvwx",
        "upload_url": UPLOAD_URL,
        "expires_at": "2026-11-18T12:15:00Z",
        "max_bytes": 8_388_608,
        "command": f'curl -sS -T FILE "{UPLOAD_URL}"',
    }
    assert service.paths() == ["/v1/uploads"]


async def test_a_carousel_renders_every_slide_and_lists_their_urls_in_order():
    service = FakeService()
    async with connected(service) as (remote, token):
        result = await tool(
            remote.client, token, "render_carousel", CAROUSEL | {"icon_color": "text", "pdf": True}
        )
    assert result["isError"] is False
    cover, first, second, closing = service.bodies
    assert cover == {
        "template": "demo/carousel-cover",
        "headline": "Drei Dinge",
        "topic": "Thema",
        "output": "url",
    }
    assert first["template"] == "demo/carousel-content"
    assert first["pagination"] == "2/4"
    assert first["icon"] == base64.b64encode(SVG.encode()).decode()
    assert first["icon_color"] == "text"
    assert second["icon_url"] == "https://example.com/icon.svg"
    assert closing == {
        "template": "demo/carousel-closing",
        "headline": "Fertig",
        "cta": "Und du?",
        "pagination": "4/4",
        "output": "url",
    }
    [document] = service.documents
    assert document["output"] == "url"
    assert [slide["template"] for slide in document["slides"]] == [
        body["template"] for body in service.bodies
    ]
    assert "output" not in document["slides"][0]
    text, image = result["content"]
    lines = text["text"].splitlines()
    assert lines[0] == "Rendered 4 slides (1080x1350), in order:"
    assert lines[1] == f"1. {STORED}01.png (demo/carousel-cover)"
    assert lines[2] == f"2. {STORED}02.png (demo/carousel-content, pagination 2/4, icon)"
    assert lines[5] == f"PDF with all 4 slides, one page each: {STORED}document.pdf"
    assert lines[6] == "The URLs work until 2026-11-19T12:00:01Z."
    assert Image.open(BytesIO(base64.b64decode(image["data"]))).width == 4 * 270 + 3 * 8


async def test_the_carousel_bundle_is_the_one_that_offers_carousels():
    service = FakeService()
    service.templates.append({"id": "acme/card"})
    async with connected(service) as (remote, token):
        await tool(remote.client, token, "render_carousel", CAROUSEL)
    assert service.bodies[0]["template"] == "demo/carousel-cover"
    assert service.paths()[0] == "/v1/templates"


def carousel_templates(bundle: str) -> list[dict[str, str]]:
    return [
        {"id": f"{bundle}/{name}"}
        for name in ("carousel-cover", "carousel-content", "carousel-closing")
    ]


@pytest.mark.parametrize(
    ("templates", "expected"),
    [
        ([{"id": "demo/card"}, {"id": "demo/carousel-cover"}], "None of your bundles offers"),
        (
            [*carousel_templates("demo"), *carousel_templates("acme")],
            "Several of your bundles offer carousels: acme, demo. Pass one as bundle.",
        ),
    ],
)
async def test_without_one_bundle_of_carousels_the_tool_asks_for_one(templates, expected):
    service = FakeService()
    service.templates = templates
    async with connected(service) as (remote, token):
        result = await tool(remote.client, token, "render_carousel", CAROUSEL)
    assert result["isError"] is True
    assert expected in text_of(result)
    assert service.bodies == []


async def test_a_named_bundle_needs_no_listing():
    service = FakeService()
    async with connected(service) as (remote, token):
        await tool(remote.client, token, "render_carousel", CAROUSEL | {"bundle": "acme"})
    assert "/v1/templates" not in service.paths()
    assert service.bodies[0]["template"] == "acme/carousel-cover"


async def test_a_slide_with_two_sources_is_refused_before_any_request():
    service = FakeService()
    slides = [{"headline": "Eins", "image_url": "https://example.com/a.png", "icon_svg": SVG}]
    async with connected(service) as (remote, token):
        result = await tool(remote.client, token, "render_carousel", CAROUSEL | {"slides": slides})
    assert (
        text_of(result) == "Slide 2: Pass one image or icon, not image_url and icon_svg together."
    )
    assert service.requests == []


async def test_a_failing_slide_is_named():
    service = FakeService()
    service.fail_render = 3
    async with connected(service) as (remote, token):
        result = await tool(remote.client, token, "render_carousel", CAROUSEL)
    assert text_of(result) == (
        "Slide 3 (demo/carousel-content) failed: Field 'x' is wrong. "
        "Retrying the same request fails again."
    )


async def test_the_render_tools_describe_the_limits_and_take_one_kind_of_source():
    assert LIMITS in RENDER_CARD
    assert LIMITS in RENDER_CAROUSEL
    async with connected(FakeService()) as (remote, token):
        listed = (await call(remote.client, token, "tools/list")).json()["result"]["tools"]
    annotations = {entry["name"]: entry.get("annotations", {}) for entry in listed}
    assert annotations["list_templates"]["readOnlyHint"] is True
    assert annotations["render_card"]["destructiveHint"] is False
    slide = next(entry for entry in listed if entry["name"] == "render_carousel")["inputSchema"]
    properties = slide["$defs"]["ContentSlide"]["properties"]
    assert set(properties) == {
        "headline",
        "body",
        "image_url",
        "image_upload",
        "icon_svg",
        "icon_url",
        "icon_upload",
    }


async def test_rendering_logs_no_url(caplog):
    caplog.set_level(logging.DEBUG)
    service = FakeService()
    async with connected(service) as (remote, token):
        await tool(remote.client, token, "render_card", {"headline": "Hallo"})
        await tool(remote.client, token, "render_carousel", CAROUSEL)
        await tool(remote.client, token, "create_upload")
    for record in caplog.records:
        assert "files.test" not in record.getMessage(), record.getMessage()
    assert any("tenant=default" in record.getMessage() for record in caplog.records)
