from tests.conftest import (
    AUTH,
    BUNDLE,
    CARD,
    CARD_IMAGE,
    CAROUSEL,
    CLOSING,
    CODE,
    CONTENT,
    COVER,
    QUOTE,
    SHOWCASE,
    SNIPPET,
    registry_of,
)


async def test_templates_lists_every_template(client):
    response = await client.get("/v1/templates", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert [t["id"] for t in body["templates"]] == [
        CARD,
        CARD_IMAGE,
        CLOSING,
        CONTENT,
        COVER,
        CODE,
        QUOTE,
    ]


async def test_every_template_names_its_bundle(client):
    body = (await client.get("/v1/templates", headers=AUTH)).json()
    for template in body["templates"]:
        assert template["id"] == f"{template['bundle']}/{template['name']}"


async def test_the_bundles_are_listed_with_their_description(client, app):
    body = (await client.get("/v1/templates", headers=AUTH)).json()
    description = registry_of(app).bundle(BUNDLE).description
    assert description
    assert body["bundles"] == [{"name": BUNDLE, "description": description}]


async def test_the_card_supports_only_portrait(client):
    body = (await client.get("/v1/templates", headers=AUTH)).json()
    card = body["templates"][0]
    assert card["id"] == CARD
    assert card["formats"] == [{"format": "4:5", "width": 1080, "height": 1350}]
    assert card["required"] == ["headline"]
    assert set(card["fields"]) == {"headline", "subline", "handle"}


async def test_carousel_templates_offer_portrait_and_square(client):
    body = (await client.get("/v1/templates", headers=AUTH)).json()
    formats = {t["id"]: t["formats"] for t in body["templates"]}
    for template_id in CAROUSEL:
        assert formats[template_id] == [
            {"format": "4:5", "width": 1080, "height": 1350},
            {"format": "1:1", "width": 1080, "height": 1080},
        ]


async def test_image_capability_is_reported(client):
    body = (await client.get("/v1/templates", headers=AUTH)).json()
    capability = {t["id"]: t["accepts_image"] for t in body["templates"]}
    assert capability == {
        CARD: False,
        CARD_IMAGE: True,
        CLOSING: False,
        CONTENT: True,
        COVER: False,
        CODE: False,
        QUOTE: True,
    }


async def test_icon_capability_is_reported(client):
    body = (await client.get("/v1/templates", headers=AUTH)).json()
    capability = {t["id"]: t["accepts_icon"] for t in body["templates"]}
    assert capability == {
        CARD: False,
        CARD_IMAGE: False,
        CLOSING: False,
        CONTENT: True,
        COVER: False,
        CODE: False,
        QUOTE: False,
    }


async def test_code_capability_is_reported(client, showcase_client):
    body = (await client.get("/v1/templates", headers=AUTH)).json()
    assert [t["id"] for t in body["templates"] if t["accepts_code"]] == [CODE]
    body = (await showcase_client.get("/v1/templates", headers=AUTH)).json()
    snippet = next(t for t in body["templates"] if t["id"] == SNIPPET)
    assert snippet["accepts_code"] is True


async def test_the_quote_requires_only_the_quote(client):
    body = (await client.get("/v1/templates", headers=AUTH)).json()
    quote = next(t for t in body["templates"] if t["id"] == QUOTE)
    assert quote["required"] == ["quote"]
    assert quote["fields"] == ["headline", "quote", "author", "role", "handle", "pagination"]


async def test_carousel_content_lists_the_icon_colour_with_its_default(client):
    body = (await client.get("/v1/templates", headers=AUTH)).json()
    content = next(t for t in body["templates"] if t["id"] == CONTENT)
    assert content["fields"] == ["headline", "body", "pagination", "handle", "icon_color"]
    assert content["defaults"]["icon_color"] == "accent"


async def test_a_template_lists_its_own_limits_and_formats(showcase_client):
    body = (await showcase_client.get("/v1/templates", headers=AUTH)).json()
    showcase = next(t for t in body["templates"] if t["id"] == SHOWCASE)
    assert showcase["formats"] == [
        {"format": "9:16", "width": 1080, "height": 1920},
        {"format": "4:5", "width": 1080, "height": 1350},
    ]
    assert showcase["limits"] == {"headline": 35, "badge": 15, "focus_x": 4}
    assert [bundle["name"] for bundle in body["bundles"]] == sorted([BUNDLE, "mini"])


async def test_only_the_quote_and_the_code_card_tighten_a_service_limit(client):
    body = (await client.get("/v1/templates", headers=AUTH)).json()
    limits = {t["id"]: t["limits"] for t in body["templates"] if t["limits"]}
    assert limits == {CODE: {"headline": 60}, QUOTE: {"headline": 80}}


async def test_samples_are_not_part_of_the_listing(client):
    body = (await client.get("/v1/templates", headers=AUTH)).json()
    assert all("samples" not in t for t in body["templates"])


async def test_response_carries_no_channel_list(client):
    body = (await client.get("/v1/templates", headers=AUTH)).json()
    assert "channels" not in body
