import base64

import pytest

from app.config import settings
from tests.conftest import AUTH, CARD_IMAGE, CONTENT, SNIPPET, image_base64, svg_base64


def slide(**fields) -> dict:
    return {"template": CONTENT, "headline": "Kein klares Ziel"} | fields


async def post(client, body, headers=AUTH):
    return await client.post("/v1/document", headers=headers, json=body)


async def test_a_valid_document_reaches_the_renderer(client):
    response = await post(client, {"slides": [slide(pagination="2/3"), slide(pagination="3/3")]})
    assert response.status_code == 503
    assert response.json()["error"] == "Renderer is not available."


async def test_a_slide_with_code_reaches_the_renderer(showcase_client):
    code = {"template": SNIPPET, "headline": "Hallo", "code": "print(1)", "language": "python"}
    response = await post(showcase_client, {"slides": [code, code]})
    assert response.status_code == 503


async def test_code_on_a_slide_without_code_is_located(client):
    response = await post(client, {"slides": [slide(), slide(code="print(1)")]})
    assert response.status_code == 400
    assert response.json()["error"] == f"Slide 2: Template {CONTENT!r} does not accept code."


async def test_a_document_needs_a_key(client):
    assert (await post(client, {"slides": [slide()]}, headers={})).status_code == 401


@pytest.mark.parametrize("count", [0, 21])
async def test_the_number_of_slides_is_limited(client, count):
    response = await post(client, {"slides": [slide()] * count})
    assert response.status_code == 400
    assert response.json()["error"].startswith("slides:")


async def test_an_unknown_top_level_key_is_rejected(client):
    response = await post(client, {"slides": [slide()], "format": "4:5"})
    assert response.status_code == 400
    assert response.json()["error"].startswith("format:")


async def test_a_slide_failing_validation_is_located(client):
    response = await post(client, {"slides": [slide(), slide(headline="x" * 141)]})
    assert response.status_code == 400
    assert response.json()["error"].startswith("slides.1.headline:")


async def test_a_slide_failing_field_resolution_is_numbered(client):
    response = await post(client, {"slides": [slide(), slide(cta="Und du?")]})
    assert response.status_code == 400
    assert response.json()["error"] == (
        f"Slide 2: Field 'cta' is not accepted by template {CONTENT!r}. "
        "Accepted: headline, body, pagination, handle, icon_color."
    )


async def test_an_unknown_template_is_numbered(client):
    response = await post(client, {"slides": [slide(template="nope")]})
    assert response.status_code == 400
    assert response.json()["error"].startswith("Slide 1: Unknown template 'nope'.")


async def test_all_slides_share_one_format(client):
    response = await post(client, {"slides": [slide(format="4:5"), slide(format="1:1")]})
    assert response.status_code == 400
    assert response.json()["error"] == "All slides must use the same format."


async def test_the_default_format_counts_as_that_format(client):
    response = await post(client, {"slides": [slide(), slide(format="4:5")]})
    assert response.status_code == 503


def fake_png(size: int) -> str:
    # Only the signature is sniffed, so padding makes an image of any size.
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * size).decode("ascii")


async def test_slides_can_carry_an_image_or_an_icon(client):
    body = {
        "slides": [
            slide(image=image_base64()),
            slide(icon=svg_base64(), icon_color="text"),
            {"template": CARD_IMAGE, "headline": "Hallo", "image": image_base64()},
        ]
    }
    assert (await post(client, body)).status_code == 503


async def test_a_slide_with_an_image_and_an_icon_is_numbered(client):
    response = await post(
        client, {"slides": [slide(), slide(image=image_base64(), icon=svg_base64())]}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "Slide 2: Send either an image or an icon, not both."


async def test_the_images_of_a_document_share_one_budget(client, monkeypatch):
    monkeypatch.setattr(settings, "max_image_bytes", 65_536)
    assert (await post(client, {"slides": [slide(image=fake_png(40_000))]})).status_code == 503

    response = await post(client, {"slides": [slide(image=fake_png(40_000))] * 2})
    assert response.status_code == 413
    assert response.json()["error"] == (
        "The images and icons of a document must be at most 64 KB together."
    )


async def test_card_image_without_an_image_is_allowed(client):
    response = await post(client, {"slides": [{"template": CARD_IMAGE, "headline": "Hallo"}]})
    assert response.status_code == 503
