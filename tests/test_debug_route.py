import base64
import html
import re
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from app.images import decode_icon, decode_image
from tests.conftest import BUNDLE, CARD, CONTENT, SHOWCASE, registry_of


async def test_debug_html_renders_without_auth(client, app):
    response = await client.get("/v1/debug/html")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert registry_of(app).get(CARD).samples["headline"] in response.text


async def test_debug_html_accepts_overrides(client):
    response = await client.get(
        "/v1/debug/html", params={"headline": "Test", "subline": "", "fit": 0}
    )
    assert response.status_code == 200
    assert "var FIT = false;" in response.text
    assert 'id="subline"' not in response.text


async def test_debug_html_rejects_unsupported_format(client):
    response = await client.get("/v1/debug/html", params={"format": "16:9"})
    assert response.status_code == 400


async def test_every_template_previews_from_its_samples(client, app):
    for template_id in registry_of(app).ids:
        response = await client.get("/v1/debug/html", params={"template": template_id})
        assert response.status_code == 200, f"{template_id}: {response.text}"


async def test_debug_html_takes_any_field_the_template_declares(client):
    response = await client.get(
        "/v1/debug/html", params={"template": CONTENT, "body": "Hallo Welt"}
    )
    assert response.status_code == 200
    assert "Hallo Welt" in response.text


async def test_debug_html_renders_the_pagination(client):
    response = await client.get(
        "/v1/debug/html", params={"template": CONTENT, "pagination": "3 von 7"}
    )
    assert response.status_code == 200
    assert '<span class="pagination">3 von 7</span>' in response.text


@pytest.mark.parametrize(
    ("field", "length"), [("headline", 141), ("handle", 61), ("pagination", 17)]
)
async def test_debug_html_keeps_the_typed_limits(client, field, length):
    response = await client.get("/v1/debug/html", params={"template": CONTENT, field: "x" * length})
    assert response.status_code == 400
    assert response.json()["error"].startswith(f"{field}:")


async def test_debug_html_rejects_a_field_the_template_does_not_declare(client):
    response = await client.get("/v1/debug/html", params={"template": CONTENT, "subline": "x"})
    assert response.status_code == 400
    assert f"'subline' is not accepted by template {CONTENT!r}" in response.json()["error"]


async def test_debug_html_keeps_the_template_limits(showcase_client):
    response = await showcase_client.get(
        "/v1/debug/html", params={"template": SHOWCASE, "badge": "x" * 16}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "Field 'badge' must be at most 15 characters."


async def test_debug_html_points_the_icon_at_its_url(client):
    response = await client.get(
        "/v1/debug/html",
        params={"template": CONTENT, "icon_url": "https://example.test/i.svg"},
    )
    assert response.status_code == 200
    assert 'class="graphic graphic-icon" id="graphic" src="https://example.test/i.svg"' in (
        response.text
    )
    assert 'data-color="accent"' in response.text


async def test_debug_html_takes_the_icon_colour(client):
    response = await client.get(
        "/v1/debug/html",
        params={
            "template": CONTENT,
            "icon_url": "https://example.test/i.svg",
            "icon_color": "text",
        },
    )
    assert response.status_code == 200
    assert 'data-color="text"' in response.text


async def test_debug_html_ignores_an_icon_the_template_cannot_show(client):
    response = await client.get("/v1/debug/html", params={"icon_url": "https://example.test/i.svg"})
    assert response.status_code == 200
    assert "example.test" not in response.text


async def test_debug_html_refuses_an_image_and_an_icon(client):
    response = await client.get(
        "/v1/debug/html",
        params={
            "template": CONTENT,
            "image_url": "https://example.test/a.png",
            "icon_url": "https://example.test/i.svg",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "Send either an image or an icon, not both."


HREF = re.compile(r'href="([^"]+)"')


async def index_links(client) -> list[str]:
    response = await client.get("/v1/debug")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    return [html.unescape(href) for href in HREF.findall(response.text)]


def templates_with(links: list[str], parameter: str) -> set[str]:
    queries = [parse_qs(urlsplit(link).query) for link in links]
    return {query["template"][0] for query in queries if parameter in query}


async def test_debug_index_links_every_template_in_every_format(client, app):
    links = await index_links(client)
    for spec in registry_of(app).specs:
        for fmt in spec.formats:
            assert f"/v1/debug/html?template={spec.id}&format={fmt.value}" in links


async def test_every_link_of_the_debug_index_works(client):
    links = await index_links(client)
    for link in links:
        response = await client.get(link)
        assert response.status_code == 200, f"{link}: {response.text}"


async def test_debug_index_offers_graphics_only_where_a_template_takes_them(client):
    links = await index_links(client)
    assert templates_with(links, "icon_url") == {CONTENT}
    assert templates_with(links, "image_url") == {CONTENT}
    assert templates_with(links, "icon_color") == {CONTENT}


async def test_debug_index_shows_a_template_with_its_required_fields_only(client):
    links = await index_links(client)
    assert f"/v1/debug/html?template={CONTENT}&format=4:5&body=&pagination=&handle=" in links


async def test_debug_index_groups_the_templates_by_bundle(client, app):
    response = await client.get("/v1/debug")
    assert f"<h2>Bundle {BUNDLE}</h2>" in response.text
    assert registry_of(app).bundle(BUNDLE).description in response.text


async def test_the_sample_icon_meets_the_icon_rules(client):
    response = await client.get("/v1/debug/samples/icon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert decode_icon(base64.b64encode(response.content).decode("ascii"))


async def test_the_sample_image_is_a_png_drawn_in_memory(client):
    response = await client.get("/v1/debug/samples/image.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert decode_image(base64.b64encode(response.content).decode("ascii"))
    with Image.open(BytesIO(response.content)) as image:
        assert image.size == (1200, 675)
        assert image.getpixel((0, 0)) != image.getpixel((0, 674))
