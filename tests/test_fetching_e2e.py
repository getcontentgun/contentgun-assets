import base64
from io import BytesIO

import pytest
import pytest_asyncio
from PIL import Image

from app.main import create_app
from tests.conftest import AUTH, CARD_IMAGE, image_base64, running
from tests.fake_web import PUBLIC_V4, FakeWeb, serve

pytestmark = [pytest.mark.requires_chromium, pytest.mark.asyncio(loop_scope="module")]

HOST = "images.example"
PHOTO_URL = f"https://{HOST}/red.jpg"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live():
    web = FakeWeb()
    web.add_host(HOST, PUBLIC_V4)
    photo = base64.b64decode(image_base64((1080, 1350), (255, 0, 0), "JPEG"))
    web.pages[PHOTO_URL] = serve(photo, headers={"Content-Type": "application/octet-stream"})
    async with running(create_app(remote=web.remote())) as client:
        yield client


async def test_an_image_from_a_url_lands_in_the_card(live):
    response = await live.post(
        "/v1/render",
        headers=AUTH,
        json={"template": CARD_IMAGE, "headline": "Hallo", "image_url": PHOTO_URL},
    )
    assert response.status_code == 200, response.text
    # A red photo under the slate gradient: red stays the strongest channel by far.
    red, green, blue = Image.open(BytesIO(response.content)).convert("RGB").getpixel((20, 20))
    assert red > 100 and green < 60 and blue < 60
