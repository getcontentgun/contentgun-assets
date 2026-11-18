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


STORED_ANSWER = {
    "ok": True,
    "url": "https://files.test/o/acme/" + "A" * 32 + "/demo-card_4x5_1a2b3c4d.png",
    "content_type": "image/png",
    "width": 1080,
    "height": 1350,
    "bytes": 1234,
    "expires_at": "2026-11-19T12:00:00Z",
}


async def test_a_client_for_a_tenant_names_it_and_leaves_the_original_alone():
    seen = []

    def handler(request):
        seen.append(request.headers.get("x-contentgun-tenant"))
        return httpx.Response(200, json={"ok": True, "templates": TEMPLATES})

    client = client_for(handler)
    await client.for_tenant("acme").list_templates()
    await client.list_templates()
    assert seen == ["acme", None]


async def test_a_stored_render_asks_for_a_url_and_reads_the_answer():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=STORED_ANSWER, headers={"x-request-id": "r-1"})

    stored = await client_for(handler).render_stored({"headline": "Hallo"})
    assert sent == [{"headline": "Hallo", "output": "url"}]
    assert (stored.url, stored.width, stored.height, stored.size) == (
        STORED_ANSWER["url"],
        1080,
        1350,
        1234,
    )
    assert (stored.expires_at, stored.request_id, stored.pages) == (
        "2026-11-19T12:00:00Z",
        "r-1",
        None,
    )


@pytest.mark.parametrize(
    "answer",
    [
        {key: value for key, value in STORED_ANSWER.items() if key != "width"},
        STORED_ANSWER | {"width": "1080"},
        STORED_ANSWER | {"bytes": True},
    ],
)
async def test_a_stored_render_with_a_malformed_answer_is_unexpected(answer):
    client = client_for(lambda request: httpx.Response(200, json=answer))
    with pytest.raises(AssetsApiError, match="Unexpected response from http://assets.test"):
        await client.render_stored({"headline": "Hallo"})


async def test_a_stored_document_must_name_its_pages():
    with_pages = client_for(lambda request: httpx.Response(200, json=STORED_ANSWER | {"pages": 3}))
    assert (await with_pages.document_stored([{"headline": "Hallo"}])).pages == 3
    without = client_for(lambda request: httpx.Response(200, json=STORED_ANSWER))
    with pytest.raises(AssetsApiError, match="Unexpected response"):
        await without.document_stored([{"headline": "Hallo"}])


async def test_an_upload_is_read_from_the_answer():
    answer = {
        "ok": True,
        "upload_id": "up_x",
        "upload_url": "https://files.test/u/token",
        "expires_at": "2026-11-18T12:15:00Z",
        "max_bytes": 100,
    }
    upload = await client_for(lambda request: httpx.Response(200, json=answer)).create_upload()
    assert (upload.upload_id, upload.upload_url, upload.max_bytes) == (
        "up_x",
        "https://files.test/u/token",
        100,
    )
    broken = client_for(lambda request: httpx.Response(200, json=answer | {"max_bytes": "100"}))
    with pytest.raises(AssetsApiError, match="Unexpected response"):
        await broken.create_upload()


async def test_a_stored_file_comes_from_the_service_without_a_key():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    data = await client_for(handler).for_tenant("acme").stored_file(STORED_ANSWER["url"])
    [request] = seen
    assert data == PNG
    assert str(request.url) == f"{BASE_URL}/o/acme/{'A' * 32}/demo-card_4x5_1a2b3c4d.png"
    assert "authorization" not in request.headers
    assert "x-contentgun-tenant" not in request.headers


async def test_only_a_stored_result_is_fetched():
    seen = []
    client = client_for(lambda request: seen.append(request) or httpx.Response(200))
    with pytest.raises(AssetsApiError, match="no stored result"):
        await client.stored_file("https://files.test/v1/templates")
    assert seen == []
