import base64
import re
from io import BytesIO
from urllib.parse import urlsplit

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from PIL import Image
from pypdf import PdfReader

from app.main import create_app
from tests.conftest import (
    CARD,
    CARD_IMAGE,
    FILES_URL,
    MISSING_BROWSER_MARKERS,
    SQUARE_SVG,
    files_of,
    image_base64,
    tenants_for,
)
from tests.mcp_remote import INTERNAL_KEY, running, sign_in, tool
from tests.memory_storage import MemoryStorage

pytestmark = [pytest.mark.requires_chromium, pytest.mark.asyncio(loop_scope="module")]

STORED_PNG = re.compile(rf"{re.escape(FILES_URL)}/o/default/\S+\.png")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def service():
    """The real service with Chromium and storage, as the transport of the remote server."""
    app = create_app(tenants_for(internal_key=INTERNAL_KEY), files=files_of(MemoryStorage()))
    manager = LifespanManager(app, startup_timeout=60)
    try:
        await manager.__aenter__()
    except Exception as exc:
        if not any(marker in str(exc).lower() for marker in MISSING_BROWSER_MARKERS):
            raise
        pytest.skip(f"Playwright Chromium is not installed: {exc}")
    try:
        yield httpx.ASGITransport(app=manager.app)
    finally:
        await manager.__aexit__(None, None, None)


async def fetch(service, url: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=service, base_url=FILES_URL) as client:
        return await client.get(urlsplit(url).path)


async def test_a_card_comes_back_as_a_url_of_the_service_with_a_preview(service):
    async with running(transport=service) as remote:
        token = (await sign_in(remote)).access
        result = await tool(
            remote.client, token, "render_card", {"template": CARD, "headline": "Hallo von Claude"}
        )
    assert result["isError"] is False, result
    text, image = result["content"]
    url = STORED_PNG.search(text["text"]).group(0)
    assert Image.open(BytesIO(base64.b64decode(image["data"]))).size == (540, 675)
    served = await fetch(service, url)
    assert served.status_code == 200
    assert Image.open(BytesIO(served.content)).size == (1080, 1350)


async def test_a_file_uploaded_from_the_chat_lands_on_the_card(service):
    photo = base64.b64decode(image_base64((1080, 1350), (255, 0, 0)))
    async with running(transport=service) as remote:
        token = (await sign_in(remote)).access
        ticket = (await tool(remote.client, token, "create_upload"))["structuredContent"]
        async with httpx.AsyncClient(transport=service, base_url=FILES_URL) as container:
            uploaded = await container.put(urlsplit(ticket["upload_url"]).path, content=photo)
        result = await tool(
            remote.client,
            token,
            "render_card",
            {"template": CARD_IMAGE, "headline": "Upload", "image_upload": ticket["upload_id"]},
        )
    assert uploaded.status_code == 201
    assert ticket["command"] == f'curl -sS -T FILE "{ticket["upload_url"]}"'
    assert result["isError"] is False, result
    url = STORED_PNG.search(result["content"][0]["text"]).group(0)
    card = Image.open(BytesIO((await fetch(service, url)).content)).convert("RGB")
    red, green, blue = card.getpixel((20, 20))
    assert red > 100 and green < 60 and blue < 60


async def test_a_carousel_with_a_pictogram_and_a_pdf(service):
    arguments = {
        "cover": {"headline": "Drei Dinge"},
        "slides": [
            {"headline": "Eins", "body": "Der erste Punkt.", "icon_svg": SQUARE_SVG},
            {"headline": "Zwei", "body": "Der zweite Punkt.", "icon_svg": SQUARE_SVG},
        ],
        "closing": {"headline": "Fertig", "cta": "Und du?"},
        "pdf": True,
    }
    async with running(transport=service) as remote:
        token = (await sign_in(remote)).access
        result = await tool(remote.client, token, "render_carousel", arguments)
    assert result["isError"] is False, result
    text = result["content"][0]["text"]
    assert len(STORED_PNG.findall(text)) == 4
    pdf_url = re.search(rf"{re.escape(FILES_URL)}/o/default/\S+\.pdf", text).group(0)
    document = await fetch(service, pdf_url)
    assert document.headers["content-type"] == "application/pdf"
    assert len(PdfReader(BytesIO(document.content)).pages) == 4
    assert result["content"][1]["mimeType"] == "image/jpeg"
