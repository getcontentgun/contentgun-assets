import asyncio
import base64
import time
from urllib.parse import urlsplit

import pytest

from app.config import settings
from app.main import create_app
from tests.conftest import (
    AUTH,
    CARD,
    CARD_IMAGE,
    CONTENT,
    FILES_URL,
    SQUARE_SVG,
    client_of,
    files_of,
    image_base64,
    svg_base64,
)
from tests.fake_web import PUBLIC_V4, FakeWeb, response, serve, streamed
from tests.memory_storage import MemoryStorage

PNG = base64.b64decode(image_base64())
SVG = SQUARE_SVG.encode("utf-8")
HOST = "images.example"
IMAGE_URL = f"https://{HOST}/photo.png"
ICON_URL = f"https://{HOST}/icon.svg"
NOT_AVAILABLE = (503, "Renderer is not available.")
OVER_BUDGET = "The images and icons of a document must be at most 64 KB together."


@pytest.fixture(scope="module")
def shared_web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture(scope="module")
def web_app(shared_web):
    """The service of the suite, with storage, fetching from the fake web."""
    return create_app(files=files_of(MemoryStorage()), remote=shared_web.remote())


@pytest.fixture
def web(shared_web) -> FakeWeb:
    shared_web.reset()
    shared_web.add_host(HOST, PUBLIC_V4)
    shared_web.pages[IMAGE_URL] = serve(PNG)
    shared_web.pages[ICON_URL] = serve(SVG)
    return shared_web


@pytest.fixture
async def client(web_app, web):
    async with client_of(web_app) as instance:
        yield instance


async def render(client, **payload):
    body = {"headline": "Hallo"} | payload
    return await client.post("/v1/render", headers=AUTH, json=body)


async def document(client, *slides):
    return await client.post("/v1/document", headers=AUTH, json={"slides": list(slides)})


def outcome(response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]


def slide(**payload) -> dict:
    return {"template": CONTENT, "headline": "Kein klares Ziel"} | payload


async def upload(client, data: bytes) -> str:
    ticket = (await client.post("/v1/uploads", headers=AUTH)).json()
    path = urlsplit(ticket["upload_url"]).path
    assert ticket["upload_url"].startswith(FILES_URL)
    assert (await client.put(path, content=data)).status_code == 201
    return ticket["upload_id"]


async def test_a_render_takes_an_image_from_a_url(client, web):
    assert outcome(await render(client, template=CARD_IMAGE, image_url=IMAGE_URL)) == NOT_AVAILABLE
    assert len(web.requests) == 1


async def test_a_render_takes_an_icon_from_a_url(client, web):
    assert outcome(await render(client, template=CONTENT, icon_url=ICON_URL)) == NOT_AVAILABLE
    assert len(web.requests) == 1


async def test_an_image_with_a_wrong_content_type_is_accepted(client, web):
    web.pages[IMAGE_URL] = serve(PNG, headers={"Content-Type": "text/html"})
    assert outcome(await render(client, template=CARD_IMAGE, image_url=IMAGE_URL)) == NOT_AVAILABLE


async def test_html_that_claims_to_be_a_png_is_refused(client, web):
    web.pages[IMAGE_URL] = serve(
        b"<html><body>Hi</body></html>", headers={"Content-Type": "image/png"}
    )
    assert outcome(await render(client, template=CARD_IMAGE, image_url=IMAGE_URL)) == (
        400,
        "The file at 'image_url' must be a PNG, JPEG or WebP image. Send an SVG as 'icon_url'.",
    )


async def test_an_image_at_an_icon_url_is_refused(client, web):
    web.pages[ICON_URL] = serve(PNG, headers={"Content-Type": "image/svg+xml"})
    assert outcome(await render(client, template=CONTENT, icon_url=ICON_URL)) == (
        400,
        "The file at 'icon_url' must be an SVG document. Send a PNG, JPEG or WebP as 'image_url'.",
    )


async def test_an_icon_from_a_url_meets_the_svg_rules(client, web):
    web.pages[ICON_URL] = serve(b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
    assert outcome(await render(client, template=CONTENT, icon_url=ICON_URL)) == (
        400,
        "The file at 'icon_url' needs a viewBox with a positive width and height.",
    )


@pytest.mark.parametrize(
    ("kind", "sources"),
    [
        ("image", {"image": image_base64(), "image_url": IMAGE_URL}),
        ("image", {"image_upload": "up_" + "a" * 24, "image_url": IMAGE_URL}),
        ("icon", {"icon": svg_base64(), "icon_url": ICON_URL}),
        ("icon", {"icon_upload": "up_" + "a" * 24, "icon_url": ICON_URL}),
    ],
)
async def test_a_graphic_comes_from_one_source(client, web, kind, sources):
    response = await render(client, template=CONTENT, **sources)
    named = ", icon_url or icon_name" if kind == "icon" else f" or {kind}_url"
    assert outcome(response) == (
        400,
        f"Send the {kind} in one field only: {kind}, {kind}_upload{named}.",
    )
    assert web.lookups == []


async def test_an_image_url_and_an_icon_are_refused_together(client, web):
    response = await render(client, template=CONTENT, image_url=IMAGE_URL, icon=svg_base64())
    assert outcome(response) == (400, "Send either an image or an icon, not both.")
    assert web.lookups == []


@pytest.mark.parametrize(("field", "url"), [("image_url", IMAGE_URL), ("icon_url", ICON_URL)])
async def test_a_template_without_the_capability_refuses_a_url_before_resolving_it(
    client, web, field, url
):
    kind = field.removesuffix("_url")
    response = await render(client, template=CARD, **{field: url})
    assert outcome(response) == (400, f"Template {CARD!r} does not accept an {kind}.")
    assert web.lookups == []
    assert web.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/photo.png",
        "https://[::1]/photo.png",
        "https://169.254.169.254/latest/meta-data/",
        f"http://{HOST}/photo.png",
        f"https://{HOST}:8443/photo.png",
        f"https://user:secret@{HOST}/photo.png",
    ],
)
async def test_a_url_against_the_rules_is_a_400(client, web, url):
    response = await render(client, template=CARD_IMAGE, image_url=url)
    assert outcome(response) == (400, "The image URL points to an address that is not allowed.")
    assert web.lookups == []


async def test_a_name_of_the_inside_is_a_400(client, web):
    web.add_host("intranet.example", "10.1.2.3")
    response = await render(
        client, template=CARD_IMAGE, image_url="https://intranet.example/photo.png"
    )
    assert outcome(response) == (400, "The image URL points to an address that is not allowed.")
    assert web.requests == []


async def test_a_url_that_cannot_be_fetched_is_a_502(client, web):
    response = await render(client, template=CARD_IMAGE, image_url=f"https://{HOST}/missing.png")
    assert outcome(response) == (502, "The image URL could not be fetched.")


async def test_an_icon_url_that_cannot_be_fetched_is_a_502(client, web):
    response = await render(client, template=CONTENT, icon_url="https://nowhere.example/i.svg")
    assert outcome(response) == (502, "The icon URL could not be fetched.")


async def test_a_file_over_the_limit_is_a_413(client, web, monkeypatch):
    monkeypatch.setattr(settings, "max_image_bytes", 65_536)
    web.pages[IMAGE_URL] = serve(PNG + b"x" * 70_000)
    response = await render(client, template=CARD_IMAGE, image_url=IMAGE_URL)
    assert outcome(response) == (413, "The file at 'image_url' must be at most 64 KB.")


async def test_a_url_is_at_most_2048_characters(client, web):
    response = await render(
        client, template=CARD_IMAGE, image_url=f"https://{HOST}/{'a' * 2048}.png"
    )
    assert outcome(response) == (400, "image_url: String should have at most 2048 characters.")


async def test_an_empty_url_counts_as_not_sent(client, web):
    assert outcome(await render(client, template=CARD_IMAGE, image_url="  ")) == NOT_AVAILABLE
    assert outcome(await render(client, template=CARD, image_url="")) == NOT_AVAILABLE
    assert web.lookups == []


async def test_a_document_fetches_each_url_once(client, web):
    response = await document(
        client, slide(image_url=IMAGE_URL), slide(image_url=IMAGE_URL), slide(image_url=IMAGE_URL)
    )
    assert outcome(response) == NOT_AVAILABLE
    assert len(web.requests) == 1


async def test_a_document_fetches_at_most_four_urls_at_once(client, web):
    running, peak = 0, 0

    async def slow(request):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return response(PNG)

    for number in range(6):
        web.pages[f"https://{HOST}/{number}.png"] = slow
    slides = [slide(image_url=f"https://{HOST}/{number}.png") for number in range(6)]
    assert outcome(await document(client, *slides)) == NOT_AVAILABLE
    assert len(web.requests) == 6
    assert peak == 4


async def test_the_budget_of_a_document_counts_urls(client, web, monkeypatch):
    monkeypatch.setattr(settings, "max_image_bytes", 65_536)
    web.pages[IMAGE_URL] = serve(PNG + b"x" * 40_000)
    inline = base64.b64encode(PNG + b"x" * 30_000).decode("ascii")
    response = await document(client, slide(image=inline), slide(image_url=IMAGE_URL))
    assert outcome(response) == (413, OVER_BUDGET)
    assert outcome(await document(client, slide(image_url=IMAGE_URL))) == NOT_AVAILABLE


async def test_the_budget_counts_a_url_once_per_slide(client, web, monkeypatch):
    monkeypatch.setattr(settings, "max_image_bytes", 65_536)
    web.pages[IMAGE_URL] = serve(PNG + b"x" * 40_000)
    response = await document(client, slide(image_url=IMAGE_URL), slide(image_url=IMAGE_URL))
    assert outcome(response) == (413, OVER_BUDGET)
    assert len(web.requests) == 1
    assert outcome(await document(client, slide(image_url=IMAGE_URL))) == NOT_AVAILABLE


async def test_a_url_on_two_slides_stops_after_half_the_budget(client, web, monkeypatch):
    monkeypatch.setattr(settings, "max_image_bytes", 65_536)
    sent = []

    async def chunks():
        yield PNG
        for _ in range(15):
            sent.append(4_096)
            yield b"x" * 4_096

    web.pages[IMAGE_URL] = streamed(chunks)
    response = await document(client, slide(image_url=IMAGE_URL), slide(image_url=IMAGE_URL))
    assert outcome(response) == (413, OVER_BUDGET)
    assert sum(sent) <= 32_768


async def test_the_budget_spans_base64_uploads_and_urls(client, web, monkeypatch):
    upload_id = await upload(client, PNG + b"x" * 25_000)
    monkeypatch.setattr(settings, "max_image_bytes", 65_536)
    web.pages[IMAGE_URL] = serve(PNG + b"x" * 25_000)
    inline = base64.b64encode(PNG + b"x" * 25_000).decode("ascii")
    slides = [slide(image=inline), slide(image_upload=upload_id), slide(image_url=IMAGE_URL)]
    assert outcome(await document(client, *slides)) == (413, OVER_BUDGET)
    assert outcome(await document(client, *slides[1:])) == NOT_AVAILABLE


async def test_running_out_of_budget_stops_every_fetch(client, web, monkeypatch):
    monkeypatch.setattr(settings, "max_image_bytes", 65_536)
    inline = base64.b64encode(PNG + b"x" * 30_000).decode("ascii")
    stopped = []

    async def large():
        yield PNG + b"x" * 40_000

    async def slow():
        try:
            while True:
                yield b"x"
                await asyncio.sleep(0.01)
        finally:
            stopped.append(True)

    web.pages[f"https://{HOST}/slow.png"] = streamed(slow)
    web.pages[f"https://{HOST}/large.png"] = streamed(large)
    started = time.monotonic()
    response = await document(
        client,
        slide(image=inline),
        slide(image_url=f"https://{HOST}/slow.png"),
        slide(image_url=f"https://{HOST}/large.png"),
    )
    assert outcome(response) == (413, OVER_BUDGET)
    assert stopped == [True]
    assert time.monotonic() - started < 5


async def test_a_url_that_fails_names_its_first_slide(client, web):
    missing = f"https://{HOST}/missing.png"
    response = await document(
        client, slide(image_url=IMAGE_URL), slide(image_url=missing), slide(image_url=missing)
    )
    assert outcome(response) == (502, "Slide 2: The image URL could not be fetched.")


async def test_a_url_against_the_rules_stops_a_document_before_any_fetch(client, web):
    response = await document(
        client, slide(image_url=IMAGE_URL), slide(image_url=f"http://{HOST}/photo.png")
    )
    assert outcome(response) == (
        400,
        "Slide 2: The image URL points to an address that is not allowed.",
    )
    assert web.requests == []


async def test_a_wrong_file_on_a_slide_is_numbered(client, web):
    web.pages[ICON_URL] = serve(PNG)
    response = await document(client, slide(), slide(icon_url=ICON_URL))
    assert outcome(response) == (
        400,
        "Slide 2: The file at 'icon_url' must be an SVG document. "
        "Send a PNG, JPEG or WebP as 'image_url'.",
    )
