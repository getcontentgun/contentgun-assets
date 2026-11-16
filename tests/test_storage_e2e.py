import base64
from io import BytesIO
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from PIL import Image
from pypdf import PdfReader

from app.main import create_app
from tests.conftest import (
    AUTH,
    CARD,
    CARD_IMAGE,
    CLOSING,
    COVER,
    FILES_URL,
    files_of,
    image_base64,
    running,
)
from tests.memory_storage import MemoryStorage

pytestmark = [pytest.mark.requires_chromium, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live(tmp_path_factory):
    app = create_app(files=files_of(MemoryStorage()))
    async with running(app) as client:
        yield client


def path_of(url: str) -> str:
    assert url.startswith(f"{FILES_URL}/")
    return urlsplit(url).path


async def test_a_card_rendered_as_url_is_served_from_it(live):
    response = await live.post(
        "/v1/render", headers=AUTH, json={"template": CARD, "headline": "Hallo", "output": "url"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["content_type"], body["width"], body["height"]) == ("image/png", 1080, 1350)
    assert "pages" not in body

    served = await live.get(path_of(body["url"]))
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert len(served.content) == body["bytes"]
    assert Image.open(BytesIO(served.content)).size == (1080, 1350)


async def test_an_uploaded_image_lands_in_the_card(live):
    ticket = (await live.post("/v1/uploads", headers=AUTH)).json()
    photo = base64.b64decode(image_base64((1080, 1350), (255, 0, 0)))
    assert (await live.put(path_of(ticket["upload_url"]), content=photo)).status_code == 201

    response = await live.post(
        "/v1/render",
        headers=AUTH,
        json={"template": CARD_IMAGE, "headline": "Hallo", "image_upload": ticket["upload_id"]},
    )
    assert response.status_code == 200, response.text
    # A red photo under the slate gradient: red stays the strongest channel by far.
    red, green, blue = Image.open(BytesIO(response.content)).convert("RGB").getpixel((20, 20))
    assert red > 100 and green < 60 and blue < 60


async def test_a_document_rendered_as_url_is_served_from_it(live):
    slides = [
        {"template": COVER, "headline": "Drei Fehler"},
        {"template": CLOSING, "headline": "Fazit", "pagination": "2/2"},
    ]
    response = await live.post(
        "/v1/document", headers=AUTH, json={"slides": slides, "output": "url"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["content_type"], body["pages"]) == ("application/pdf", 2)

    served = await live.get(path_of(body["url"]))
    assert served.status_code == 200
    assert served.headers["content-type"] == "application/pdf"
    assert len(PdfReader(BytesIO(served.content)).pages) == 2
