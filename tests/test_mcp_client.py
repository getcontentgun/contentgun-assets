import json
from pathlib import Path

import httpx
import pytest

from client import AssetsApiError, AssetsClient
from config import Config

KEY = "client-secret-key"
BASE_URL = "http://assets.test"
TEMPLATES = [{"id": "brand/card", "fields": ["headline", "subline", "handle"]}]
PNG = b"\x89PNG\r\n\x1a\n-fake"


def client_for(handler) -> AssetsClient:
    config = Config(
        api_key=KEY,
        base_url=BASE_URL,
        timeout_s=5,
        output_root=Path("/unused"),
        image_roots=(Path("/unused"),),
    )
    return AssetsClient(config, transport=httpx.MockTransport(handler))


def envelope(status: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"ok": False, "error": message})


async def failure(handler, call: str = "list_templates") -> str:
    client = client_for(handler)
    with pytest.raises(AssetsApiError) as excinfo:
        if call == "render":
            await client.render({"headline": "Hallo"})
        elif call == "document":
            await client.document([{"headline": "Hallo"}])
        else:
            await client.list_templates()
    message = str(excinfo.value)
    assert KEY not in message
    return message


async def test_list_templates_sends_the_key_and_returns_the_templates(caplog):
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["Authorization"]
        return httpx.Response(200, json={"ok": True, "templates": TEMPLATES})

    caplog.set_level("INFO")
    assert await client_for(handler).list_templates() == TEMPLATES
    assert seen == {
        "method": "GET",
        "url": f"{BASE_URL}/v1/templates",
        "authorization": f"Bearer {KEY}",
    }
    assert KEY not in caplog.text


async def test_render_posts_the_body_and_returns_the_png():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=PNG, headers={"Content-Type": "image/png", "X-Request-Id": "abc-123"}
        )

    rendered = await client_for(handler).render({"headline": "Hallo", "template": "brand/card"})
    assert rendered.data == PNG
    assert rendered.request_id == "abc-123"
    assert seen == {
        "url": f"{BASE_URL}/v1/render",
        "body": {"headline": "Hallo", "template": "brand/card"},
    }


async def test_rejected_key_names_the_variable_and_the_service():
    message = await failure(lambda request: envelope(401, "Unauthorized."))
    assert message == f"The service at {BASE_URL} rejected ASSETS_API_KEY."


async def test_the_service_sentence_is_passed_through():
    message = await failure(
        lambda request: envelope(500, "API keys are not configured on the server.")
    )
    assert message == "API keys are not configured on the server."


@pytest.mark.parametrize(
    ("status", "sentence", "hint"),
    [
        (400, "headline: String should have at most 140 characters.", "fails again"),
        (413, "Field 'image' must be at most 8 MB.", "smaller images or icons"),
        (503, "Renderer is busy, retry later.", "retrying is safe"),
        (504, "Render timed out.", "Retrying is safe"),
    ],
)
async def test_render_errors_say_whether_a_retry_helps(status, sentence, hint):
    message = await failure(lambda request: envelope(status, sentence), call="render")
    assert message.startswith(sentence)
    assert hint in message


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(502, text="<html>Bad Gateway</html>"),
        httpx.Response(200, json={"ok": True}),
        httpx.Response(200, text="not json"),
    ],
    ids=["html-502", "no-templates", "not-json"],
)
async def test_an_unexpected_response_names_the_status(response):
    message = await failure(lambda request: response)
    assert message == f"Unexpected response from {BASE_URL}: HTTP {response.status_code}."


async def test_a_render_answer_that_is_not_a_png_is_unexpected():
    message = await failure(lambda request: httpx.Response(200, json={"ok": True}), call="render")
    assert message == f"Unexpected response from {BASE_URL}: HTTP 200."


async def test_document_posts_the_slides_and_returns_the_pdf():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=b"%PDF-1.4",
            headers={"Content-Type": "application/pdf", "X-Request-Id": "r"},
        )

    slides = [{"template": "brand/carousel-cover", "headline": "Hook"}]
    rendered = await client_for(handler).document(slides)
    assert rendered.data == b"%PDF-1.4"
    assert seen == {"url": f"{BASE_URL}/v1/document", "body": {"slides": slides}}


async def test_a_document_answer_that_is_not_a_pdf_is_unexpected():
    message = await failure(
        lambda request: httpx.Response(200, content=b"x", headers={"Content-Type": "image/png"}),
        call="document",
    )
    assert message == f"Unexpected response from {BASE_URL}: HTTP 200."


async def test_a_document_error_is_passed_through():
    message = await failure(
        lambda request: envelope(400, "All slides must use the same format."), call="document"
    )
    assert message.startswith("All slides must use the same format.")


async def test_a_refused_connection_points_at_bin_development():
    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    message = await failure(handler)
    assert message == f"Could not reach {BASE_URL}. Locally the service runs with bin/development."


async def test_a_timeout_names_the_limit():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    assert await failure(handler) == f"{BASE_URL} did not answer within 5 seconds."
