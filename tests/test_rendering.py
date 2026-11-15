import pytest

from app.errors import ApiError
from app.formats import Format
from app.rendering import (
    resolve_fields,
    resolve_format,
    resolve_icon,
    resolve_image,
    resolve_template,
)
from app.templating import TemplateRegistry
from tests.conftest import (
    AUTH,
    BUNDLES_DIR,
    CARD,
    CARD_IMAGE,
    CAROUSEL,
    CONTENT,
    COVER,
    SHOWCASE,
    image_base64,
    svg_base64,
)


@pytest.fixture(scope="module")
def registry():
    return TemplateRegistry.load(BUNDLES_DIR)


def test_the_default_template_comes_from_the_settings(registry):
    assert resolve_template(registry, None).id == CARD


def test_unknown_template_raises_400(registry):
    with pytest.raises(ApiError) as excinfo:
        resolve_template(registry, "nope")
    assert excinfo.value.status_code == 400
    assert "Unknown template 'nope'" in excinfo.value.message
    assert f"Available: {CARD}, " in excinfo.value.message


def test_a_template_name_without_its_bundle_raises_400(registry):
    name = CARD.split("/")[1]
    with pytest.raises(ApiError) as excinfo:
        resolve_template(registry, name)
    assert excinfo.value.status_code == 400
    assert f"Unknown template {name!r}" in excinfo.value.message


def test_format_defaults_to_first_supported(registry):
    spec = resolve_template(registry, None)
    assert resolve_format(spec, None) is Format.PORTRAIT_4_5


def test_unsupported_format_raises_400(registry):
    spec = resolve_template(registry, None)
    with pytest.raises(ApiError) as excinfo:
        resolve_format(spec, Format.SQUARE)
    assert excinfo.value.status_code == 400
    assert excinfo.value.message == (
        f"Format '1:1' is not supported by template {CARD!r}. Supported: 4:5."
    )


def test_omitted_handle_falls_back_to_the_template_default(registry):
    spec = resolve_template(registry, None)
    fields = resolve_fields(spec, {"headline": "Hallo"})
    assert fields["handle"] == ""


def test_handle_comes_from_the_request(registry):
    spec = resolve_template(registry, None)
    fields = resolve_fields(spec, {"headline": "Hallo", "handle": "@custom"})
    assert fields["handle"] == "@custom"


def test_blank_headline_raises_400(registry):
    spec = resolve_template(registry, None)
    with pytest.raises(ApiError) as excinfo:
        resolve_fields(spec, {"headline": "   "})
    assert excinfo.value.status_code == 400


def test_field_the_template_does_not_declare_raises_400(registry):
    spec = resolve_template(registry, None)
    with pytest.raises(ApiError) as excinfo:
        resolve_fields(spec, {"headline": "Hallo", "badge": "Neu"})
    assert excinfo.value.status_code == 400
    assert f"is not accepted by template {CARD!r}" in excinfo.value.message


def test_empty_template_name_is_not_treated_as_omitted(registry):
    with pytest.raises(ApiError) as excinfo:
        resolve_template(registry, "")
    assert "Unknown template ''" in excinfo.value.message


def test_card_image_accepts_an_image(registry):
    spec = resolve_template(registry, CARD_IMAGE)
    assert resolve_image(spec, image_base64()).startswith("data:image/png;base64,")


def test_card_image_falls_back_to_its_own_image(registry):
    spec = resolve_template(registry, CARD_IMAGE)
    assert resolve_image(spec, None).startswith("data:image/jpeg;base64,")
    assert resolve_image(spec, "  ") == resolve_image(spec, None)


def test_card_dark_has_no_fallback_image(registry):
    assert resolve_image(resolve_template(registry, CARD), None) is None


def test_image_for_a_template_without_the_capability_raises_400(registry):
    spec = resolve_template(registry, CARD)
    with pytest.raises(ApiError) as excinfo:
        resolve_image(spec, image_base64())
    assert excinfo.value.status_code == 400
    assert excinfo.value.message == f"Template {CARD!r} does not accept an image."


async def test_image_sent_to_card_dark_returns_400(client):
    response = await client.post(
        "/v1/render", headers=AUTH, json={"headline": "Hallo", "image": image_base64()}
    )
    assert response.status_code == 400
    assert "does not accept an image" in response.json()["error"]


async def test_unusable_image_returns_400(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH,
        json={"template": CARD_IMAGE, "headline": "Hallo", "image": "not base64 !!"},
    )
    assert response.status_code == 400
    assert "base64-encoded image data" in response.json()["error"]


def test_carousel_content_accepts_an_image_and_an_icon(registry):
    spec = resolve_template(registry, CONTENT)
    assert resolve_image(spec, image_base64()).startswith("data:image/png;base64,")
    assert resolve_icon(spec, svg_base64()).startswith("data:image/svg+xml;base64,")


def test_carousel_content_has_no_fallback_image(registry):
    assert resolve_image(resolve_template(registry, CONTENT), None) is None


def test_a_blank_icon_counts_as_none(registry):
    spec = resolve_template(registry, CARD)
    assert resolve_icon(spec, None) is None
    assert resolve_icon(spec, "  ") is None


@pytest.mark.parametrize("template", [CARD, CARD_IMAGE, COVER])
def test_icon_for_a_template_without_the_capability_raises_400(registry, template):
    with pytest.raises(ApiError) as excinfo:
        resolve_icon(resolve_template(registry, template), svg_base64())
    assert excinfo.value.status_code == 400
    assert excinfo.value.message == f"Template {template!r} does not accept an icon."


async def test_an_image_and_an_icon_together_return_400(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH,
        json={
            "template": CONTENT,
            "headline": "Hallo",
            "image": image_base64(),
            "icon": svg_base64(),
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "Send either an image or an icon, not both."


async def test_a_blank_image_next_to_an_icon_is_no_conflict(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH,
        json={
            "template": CONTENT,
            "headline": "Hallo",
            "image": "",
            "icon": svg_base64(),
        },
    )
    assert response.status_code == 503


async def test_icon_colour_outside_the_brand_returns_400(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH,
        json={"template": CONTENT, "headline": "Hallo", "icon_color": "pink"},
    )
    assert response.status_code == 400
    assert response.json()["error"].startswith("icon_color:")


async def test_icon_colour_is_a_field_of_carousel_content_only(client):
    response = await client.post(
        "/v1/render", headers=AUTH, json={"headline": "Hallo", "icon_color": "text"}
    )
    assert response.status_code == 400
    assert f"'icon_color' is not accepted by template {CARD!r}" in response.json()["error"]


def test_icon_colour_defaults_to_the_accent(registry):
    spec = resolve_template(registry, CONTENT)
    assert resolve_fields(spec, {"headline": "Hallo"})["icon_color"] == "accent"
    assert resolve_fields(spec, {"headline": "Hallo", "icon_color": "text"})["icon_color"] == "text"


async def test_channel_is_no_longer_accepted(client):
    response = await client.post(
        "/v1/render", headers=AUTH, json={"headline": "Hallo", "channel": "instagram"}
    )
    assert response.status_code == 400
    assert f"'channel' is not accepted by template {CARD!r}" in response.json()["error"]


async def test_unsupported_format_over_api(client):
    response = await client.post(
        "/v1/render", headers=AUTH, json={"format": "1:1", "headline": "Hallo"}
    )
    assert response.status_code == 400
    assert f"not supported by template {CARD!r}" in response.json()["error"]


async def test_missing_headline_returns_400(client):
    response = await client.post("/v1/render", headers=AUTH, json={"format": "4:5"})
    assert response.status_code == 400
    assert response.json()["error"].startswith("headline:")


async def test_too_long_headline_returns_400(client):
    response = await client.post("/v1/render", headers=AUTH, json={"headline": "x" * 141})
    assert response.status_code == 400


async def test_unknown_field_returns_400(client):
    response = await client.post(
        "/v1/render", headers=AUTH, json={"headline": "Hallo", "nope": "x"}
    )
    assert response.status_code == 400
    assert f"is not accepted by template {CARD!r}" in response.json()["error"]


@pytest.mark.parametrize("name", CAROUSEL)
async def test_carousel_request_without_a_subline_reaches_the_renderer(client, name):
    response = await client.post(
        "/v1/render", headers=AUTH, json={"template": name, "headline": "Hallo"}
    )
    assert response.status_code == 503
    assert response.json()["error"] == "Renderer is not available."


async def test_full_content_slide_reaches_the_renderer(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH,
        json={
            "template": CONTENT,
            "format": "1:1",
            "headline": "Kein klares Ziel",
            "body": "Leg vorher fest, woran du Erfolg erkennst.",
            "pagination": "2/7",
            "handle": "@contentgun",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"] == "Renderer is not available."


async def test_explicit_null_subline_still_reaches_card_dark(client):
    response = await client.post(
        "/v1/render", headers=AUTH, json={"headline": "Hallo", "subline": None}
    )
    assert response.status_code == 503


async def test_explicit_null_subline_is_rejected_by_a_template_without_one(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH,
        json={"template": CONTENT, "headline": "Hallo", "subline": None},
    )
    assert response.status_code == 400
    assert f"'subline' is not accepted by template {CONTENT!r}" in response.json()["error"]


async def test_topic_at_its_limit_reaches_the_renderer(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH,
        json={"template": COVER, "headline": "Hallo", "topic": "x" * 60},
    )
    assert response.status_code == 503


async def test_too_long_topic_returns_400(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH,
        json={"template": COVER, "headline": "Hallo", "topic": "x" * 61},
    )
    assert response.status_code == 400
    assert response.json()["error"].startswith("topic:")


async def test_pagination_at_its_limit_reaches_the_renderer(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH,
        json={"template": CONTENT, "headline": "Hallo", "pagination": "x" * 16},
    )
    assert response.status_code == 503


async def test_too_long_pagination_returns_400(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH,
        json={"template": CONTENT, "headline": "Hallo", "pagination": "x" * 17},
    )
    assert response.status_code == 400
    assert response.json()["error"].startswith("pagination:")


async def test_numeric_pagination_returns_400(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH,
        json={"template": CONTENT, "headline": "Hallo", "pagination": 3},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "pagination: Input should be a valid string."


async def test_pagination_is_rejected_by_the_cover(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH,
        json={"template": COVER, "headline": "Hallo", "pagination": "1/7"},
    )
    assert response.status_code == 400
    assert f"'pagination' is not accepted by template {COVER!r}" in response.json()["error"]


async def test_malformed_json_returns_400(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH | {"Content-Type": "application/json"},
        content=b"{not json",
    )
    assert response.status_code == 400


def test_template_limits_are_tighter_than_the_service_cap(showcase_root):
    spec = resolve_template(TemplateRegistry.load(showcase_root), SHOWCASE)
    assert resolve_fields(spec, {"headline": "x" * 35})["headline"] == "x" * 35
    with pytest.raises(ApiError) as excinfo:
        resolve_fields(spec, {"headline": "x" * 36})
    assert excinfo.value.message == "Field 'headline' must be at most 35 characters."


def test_the_first_format_of_a_template_is_its_default(showcase_root):
    spec = resolve_template(TemplateRegistry.load(showcase_root), SHOWCASE)
    assert resolve_format(spec, None) is Format.STORY


async def test_a_field_over_the_template_limit_returns_400(showcase_client):
    response = await showcase_client.post(
        "/v1/render",
        headers=AUTH,
        json={"template": SHOWCASE, "headline": "Hallo", "badge": "x" * 16},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "Field 'badge' must be at most 15 characters."
