from collections import Counter
from dataclasses import replace
from io import BytesIO

import pytest
import pytest_asyncio
from PIL import Image, ImageChops
from pypdf import PdfReader

from tests.conftest import (
    ACME,
    ACME_CARD,
    AUTH,
    BUNDLE,
    CARD,
    CARD_IMAGE,
    CLOSING,
    CONTENT,
    COVER,
    DISPLAY_FACE,
    STUDIO,
    STUDIO_CARD,
    TEXT_FACE,
    image_base64,
    registry_of,
    svg_base64,
    tenant_auth,
)

pytestmark = [pytest.mark.requires_chromium, pytest.mark.asyncio(loop_scope="module")]

# The geometry and the palette of the demo bundle, from its CSS.
SLATE = (30, 42, 54)
TEXT = (243, 239, 230)
AMBER = (242, 179, 61)
PADDING_X = 96
PADDING_BOTTOM = 96
# Below the top padding, the header row with its hairline and the gap under it.
STAGE_TOP = {"4:5": 80 + 66 + 48, "1:1": 80 + 66 + 40}
BAR = (96, 12)
ARROW_SIZES = {"4:5": 104, "1:1": 88}
ICON_SIZES = {"4:5": 240, "1:1": 180}

LONG_HEADLINE = (
    "Automatisierung ist kein Selbstzweck sondern ein Werkzeug fuer bessere Entscheidungen "
    "und mehr Freiraum im Arbeitsalltag."
)

CAROUSEL = {
    COVER: {"topic": "Arbeitsweise", "subline": "Und wie du sie in deinen Alltag holst."},
    CONTENT: {
        "body": "Leg vorher fest, woran du Erfolg erkennst.",
        "pagination": "2/7",
    },
    CLOSING: {
        "cta": "Welche Gewohnheit probierst du als Erstes aus?",
        "pagination": "7/7",
    },
}
CAROUSEL_SIZES = {"4:5": (1080, 1350), "1:1": (1080, 1080)}
CAROUSEL_BACKGROUNDS = {COVER: SLATE, CONTENT: SLATE, CLOSING: SLATE}


def image_of(response) -> Image.Image:
    return Image.open(BytesIO(response.content)).convert("RGB")


def colour_mask(image: Image.Image, colour: tuple[int, int, int], tolerance: int = 6):
    bands = [
        band.point(lambda value, target=target: 255 if abs(value - target) <= tolerance else 0)
        for band, target in zip(image.split(), colour, strict=True)
    ]
    return ImageChops.multiply(ImageChops.multiply(bands[0], bands[1]), bands[2])


def colour_counts(image: Image.Image) -> list[tuple[int, tuple[int, int, int]]]:
    counts = image.getcolors(maxcolors=image.width * image.height)
    assert counts is not None
    return counts


def ink_box(image: Image.Image, background: tuple[int, int, int]):
    return ImageChops.difference(image, Image.new("RGB", image.size, background)).getbbox()


def top_mark(image: Image.Image, colour: tuple[int, int, int], top: int):
    """The topmost shape in the colour below top, measured in its first 20 rows."""
    mask = colour_mask(image.crop((0, top, image.width, image.height)), colour)
    box = mask.getbbox()
    assert box is not None, f"Nothing in {colour} below row {top}."
    band = mask.crop((0, box[1], mask.width, box[1] + 20)).getbbox()
    return (band[0], top + box[1] + band[1], band[2], top + box[1] + band[3])


def headline_ink(image: Image.Image) -> tuple[int, int, int, int]:
    # On the card, everything between the bar and the bottom padding is the headline when no
    # subline is sent.
    bar = top_mark(image, AMBER, STAGE_TOP["4:5"])
    region = image.crop((0, bar[3], image.width, image.height - PADDING_BOTTOM))
    box = ink_box(region, SLATE)
    assert box is not None, "Nothing was drawn under the bar."
    return (box[0], box[1] + bar[3], box[2], box[3] + bar[3])


def arrow_region(image: Image.Image, fmt: str) -> tuple[int, int, int, int]:
    size = ARROW_SIZES[fmt]
    right, bottom = image.width - PADDING_X, image.height - PADDING_BOTTOM
    return (right - size - 8, bottom - size - 8, right + 8, bottom + 8)


def assert_arrow_in_the_bottom_right_corner(image: Image.Image, fmt: str) -> None:
    left, top, _, _ = region = arrow_region(image, fmt)
    box = colour_mask(image.crop(region), AMBER).getbbox()
    assert box is not None, "The swipe button is missing from the cover."
    size = ARROW_SIZES[fmt]
    right, bottom = image.width - PADDING_X, image.height - PADDING_BOTTOM
    # The disc's antialiased rim is not counted, so it measures a pixel or two short.
    assert abs(left + box[0] - (right - size)) <= 2
    assert abs(left + box[2] - right) <= 2
    assert abs(top + box[1] - (bottom - size)) <= 2
    assert abs(top + box[3] - bottom) <= 2


async def render(client, **payload):
    body = {"headline": "Gute Vorlagen sparen jede Woche Stunden."} | payload
    return await client.post("/v1/render", headers=AUTH, json=body)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def slides(live_client):
    return {
        (name, fmt): await render(
            live_client, template=name, format=fmt, handle="@contentgun", **fields
        )
        for name, fields in CAROUSEL.items()
        for fmt in CAROUSEL_SIZES
    }


async def test_render_returns_portrait_png(live_client):
    response = await render(live_client, format="4:5", subline="Text rein, fertige Karte raus.")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert image_of(response).size == (1080, 1350)


async def test_format_defaults_to_portrait(live_client):
    response = await render(live_client)
    assert response.status_code == 200
    assert image_of(response).size == (1080, 1350)


async def test_response_headers(live_client):
    response = await render(live_client)
    assert len(response.headers["X-Request-Id"]) == 36
    stem = CARD.replace("/", "-")
    assert response.headers["Content-Disposition"].startswith(f'inline; filename="{stem}_4x5_')
    assert response.headers["Cache-Control"] == "no-store"


async def test_background_is_slate_and_text_is_drawn(live_client):
    image = image_of(await render(live_client, subline="Text rein, fertige Karte raus."))
    assert image.getpixel((20, 20)) == SLATE
    drawn = sum(count for count, colour in colour_counts(image) if colour != SLATE)
    assert drawn > image.width * image.height * 0.01


async def test_the_accent_bar_stands_at_the_left_edge(live_client):
    image = image_of(await render(live_client, subline="Text rein, fertige Karte raus."))
    box = top_mark(image, AMBER, STAGE_TOP["4:5"])
    assert box[0] == PADDING_X
    assert abs((box[2] - box[0]) - BAR[0]) <= 1
    assert abs((box[3] - box[1]) - BAR[1]) <= 1


async def test_padding_stays_clear(live_client):
    image = image_of(await render(live_client, headline=LONG_HEADLINE))
    for box in ((0, 0, 40, 40), (1040, 0, 1080, 40), (0, 1310, 40, 1350)):
        assert ink_box(image.crop(box), SLATE) is None


async def test_short_headline_uses_a_large_font(live_client):
    # Guards the fit-text search: a clipped line box would pin every headline to the
    # 40px minimum, which is roughly a 30px ink band instead of the 70px one below.
    box = headline_ink(image_of(await render(live_client, headline="Mut.", subline="")))
    assert box[3] - box[1] > 60


async def test_long_headline_shrinks_but_still_renders(live_client):
    response = await render(live_client, headline=LONG_HEADLINE, subline="Ein zweiter Satz.")
    assert response.status_code == 200
    image = image_of(response)
    assert image.size == (1080, 1350)
    assert headline_ink(image) is not None


async def test_umlaut_accents_are_rendered(live_client):
    plain = headline_ink(image_of(await render(live_client, headline="AOU", subline="")))
    umlaut = headline_ink(image_of(await render(live_client, headline="ÄÖÜ", subline="")))
    assert umlaut[1] < plain[1]


async def test_background_image_is_drawn_under_the_slate_gradient(live_client):
    response = await render(
        live_client,
        template=CARD_IMAGE,
        image=image_base64((1080, 1350), (255, 255, 255)),
    )
    assert response.status_code == 200, response.text
    image = image_of(response)
    assert image.size == (1080, 1350)
    # A white backdrop under the scrim: neither the raw image nor the bare card, but a light
    # grey that leans towards the slate's blue.
    red, green, blue = image.getpixel((20, 20))
    assert red < green < blue
    assert 100 < red < 170


async def test_card_image_without_an_image_uses_the_fallback(live_client):
    response = await render(live_client, template=CARD_IMAGE)
    assert response.status_code == 200, response.text
    image = image_of(response)
    assert image.size == (1080, 1350)
    # The fallback glows warm at the top right, where the bare card would stay slate blue.
    red, _, blue = image.getpixel((842, 405))
    assert red > blue + 15


async def test_undeclared_font_fails_loudly(live_client, app, monkeypatch):
    bundle = registry_of(app).bundle(BUNDLE)
    monkeypatch.setattr(bundle, "fonts", replace(bundle.fonts, checks=('400 20px "NotAFont"',)))
    response = await render(live_client)
    assert response.status_code == 500
    assert "Brand font not loaded" in response.json()["error"]


async def test_renderer_recovers_after_the_font_check_is_restored(live_client):
    assert (await render(live_client)).status_code == 200


async def test_health_reports_ready(live_client, app):
    response = await live_client.get("/health")
    assert response.status_code == 200
    assert response.json()["browser"] == "ready"
    assert response.json()["templates"] == app.state.tenants.builtin_ids


@pytest.mark.parametrize("fmt", CAROUSEL_SIZES)
@pytest.mark.parametrize("name", CAROUSEL)
async def test_carousel_slide_renders_at_its_format_size(slides, name, fmt):
    response = slides[name, fmt]
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert image_of(response).size == CAROUSEL_SIZES[fmt]


@pytest.mark.parametrize("fmt", CAROUSEL_SIZES)
@pytest.mark.parametrize("name", CAROUSEL)
async def test_carousel_slide_background(slides, name, fmt):
    assert image_of(slides[name, fmt]).getpixel((20, 20)) == CAROUSEL_BACKGROUNDS[name]


@pytest.mark.parametrize("fmt", CAROUSEL_SIZES)
@pytest.mark.parametrize("name", CAROUSEL)
async def test_carousel_slide_opens_its_stage_with_its_mark(slides, name, fmt):
    # The cover's topic label, the bar on the other slides.
    box = top_mark(image_of(slides[name, fmt]), AMBER, STAGE_TOP[fmt])
    assert box[0] == PADDING_X
    assert (box[2] - box[0]) >= BAR[0] - 1
    assert (box[3] - box[1]) >= BAR[1] - 1


@pytest.mark.parametrize("fmt", CAROUSEL_SIZES)
@pytest.mark.parametrize("name", CAROUSEL)
async def test_carousel_slide_draws_its_text(slides, name, fmt):
    image = image_of(slides[name, fmt])
    drawn = sum(n for n, colour in colour_counts(image) if colour != CAROUSEL_BACKGROUNDS[name])
    assert drawn > image.width * image.height * 0.01


@pytest.mark.parametrize("fmt", CAROUSEL_SIZES)
async def test_content_text_is_light_and_vertically_centered(slides, fmt):
    image = image_of(slides[CONTENT, fmt])
    text = sum(n for n, colour in colour_counts(image) if colour == TEXT)
    assert text > image.width * image.height * 0.01
    # Glyph ink sits a little inside its line boxes, so the two gaps match only roughly.
    stage = image.crop((0, STAGE_TOP[fmt], image.width, image.height - PADDING_BOTTOM))
    box = ink_box(stage, SLATE)
    above, below = box[1], stage.height - box[3]
    assert above > 150
    assert abs(above - below) <= 40


@pytest.mark.parametrize("fmt", CAROUSEL_SIZES)
async def test_cover_swipe_button_sits_in_the_bottom_right_corner(slides, fmt):
    assert_arrow_in_the_bottom_right_corner(image_of(slides[COVER, fmt]), fmt)


@pytest.mark.parametrize("fmt", CAROUSEL_SIZES)
@pytest.mark.parametrize("name", [CONTENT, CLOSING])
async def test_only_the_cover_draws_a_swipe_button(slides, name, fmt):
    image = image_of(slides[name, fmt])
    assert ink_box(image.crop(arrow_region(image, fmt)), CAROUSEL_BACKGROUNDS[name]) is None


CONTENT_WITH_ICON = {
    "template": CONTENT,
    "headline": "Erst das Ziel",
    "body": "Leg vorher fest, woran du Erfolg erkennst.",
    "icon": svg_base64(),
}


def graphic_box(image: Image.Image, fmt: str, colour: tuple[int, int, int]):
    # The bar is the topmost shape in the accent; everything in it further down is the icon.
    bar = top_mark(image, AMBER, STAGE_TOP[fmt])
    below = image.crop((0, bar[3] + 10, image.width, image.height - PADDING_BOTTOM))
    box = colour_mask(below, colour).getbbox()
    return None if box is None else (box[0], box[1] + bar[3] + 10, box[2], box[3] + bar[3] + 10)


def text_ink(image: Image.Image) -> int:
    return sum(n for n, colour in colour_counts(image) if colour == TEXT)


@pytest.mark.parametrize("fmt", CAROUSEL_SIZES)
async def test_content_icon_is_tinted_in_the_accent_under_the_text(live_client, fmt):
    response = await render(live_client, format=fmt, **CONTENT_WITH_ICON)
    assert response.status_code == 200, response.text
    box = graphic_box(image_of(response), fmt, AMBER)
    assert box is not None, "The icon is missing or not in the accent color."
    size = ICON_SIZES[fmt]
    assert box[0] == PADDING_X
    assert abs((box[2] - box[0]) - size) <= 1
    assert abs((box[3] - box[1]) - size) <= 1


async def test_content_icon_can_take_the_text_colour(live_client):
    accent = image_of(await render(live_client, **CONTENT_WITH_ICON))
    response = await render(live_client, **CONTENT_WITH_ICON, icon_color="text")
    assert response.status_code == 200, response.text
    text = image_of(response)
    assert graphic_box(text, "4:5", AMBER) is None
    assert text_ink(text) - text_ink(accent) == pytest.approx(240 * 240, abs=2 * 240)


async def test_content_image_is_drawn_as_sent(live_client):
    response = await render(
        live_client,
        template=CONTENT,
        headline="Erst das Ziel",
        image=image_base64((400, 200), (255, 0, 0)),
    )
    assert response.status_code == 200, response.text
    red = sum(n for n, colour in colour_counts(image_of(response)) if colour == (255, 0, 0))
    assert red == pytest.approx(400 * 200, abs=2 * 400)


async def test_cover_swipe_button_stays_without_a_handle(live_client):
    response = await render(live_client, template=COVER, handle="")
    assert response.status_code == 200, response.text
    assert_arrow_in_the_bottom_right_corner(image_of(response), "4:5")


async def test_carousel_filename_names_bundle_template_and_format(slides):
    disposition = slides[CONTENT, "1:1"].headers["Content-Disposition"]
    assert disposition.startswith(f'inline; filename="{CONTENT.replace("/", "-")}_1x1_')


DOCUMENT = [
    {
        "template": COVER,
        "headline": "Drei Gewohnheiten",
        "topic": "Arbeitsweise",
        "subline": "Und wie du sie vermeidest.",
    },
    {
        "template": CONTENT,
        "headline": "Kein Ziel",
        "body": "Leg es fest.",
        "pagination": "2/4",
    },
    {
        "template": CONTENT,
        "headline": "Zu viel",
        "body": "Starte klein.",
        "pagination": "3/4",
    },
    {"template": CLOSING, "headline": "Fazit", "cta": "Und du?", "pagination": "4/4"},
]
# The line in the text face of each slide, which identifies the page and survives text
# extraction.
PAGE_TEXT = ["Und wie du sie vermeidest.", "Leg es fest.", "Starte klein.", "Und du?"]


def compact(text: str) -> str:
    return "".join(text.split())


def font_names(reader: PdfReader) -> set[str]:
    names = set()
    for page in reader.pages:
        for font in page["/Resources"]["/Font"].values():
            font = font.get_object()
            # Chromium may embed a variable font as a Type3 font, which has no /BaseFont.
            name = font.get("/BaseFont") or font["/FontDescriptor"].get_object()["/FontName"]
            names.add(str(name))
    return names


@pytest.mark.parametrize(("fmt", "size"), [("4:5", (810, 1012.5)), ("1:1", (810, 810))])
async def test_document_has_one_page_per_slide(live_client, fmt, size):
    slides = [slide | {"format": fmt} for slide in DOCUMENT]
    response = await live_client.post("/v1/document", headers=AUTH, json={"slides": slides})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    token = fmt.replace(":", "x")
    assert response.headers["Content-Disposition"].startswith(
        f'inline; filename="document_{token}_'
    )

    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) == len(DOCUMENT)
    for page, text in zip(reader.pages, PAGE_TEXT, strict=True):
        # 1080 CSS px at 96 dpi are 810 pt; Chromium rounds the height by a fraction of a point.
        box = (float(page.mediabox.width), float(page.mediabox.height))
        assert box == pytest.approx(size, abs=1)
        assert compact(text) in compact(page.extract_text())


async def test_document_embeds_the_fonts_of_the_bundle(live_client):
    response = await live_client.post("/v1/document", headers=AUTH, json={"slides": DOCUMENT})
    assert response.status_code == 200, response.text
    names = font_names(PdfReader(BytesIO(response.content)))
    assert any(DISPLAY_FACE[0].replace(" ", "") in name for name in names), names
    assert any(TEXT_FACE[0].replace(" ", "") in name for name in names), names


async def test_document_rasterises_the_icon_in_the_accent(live_client):
    # Chromium prints a filtered element as a bitmap; without the filter it would come out black.
    response = await live_client.post(
        "/v1/document", headers=AUTH, json={"slides": [CONTENT_WITH_ICON]}
    )
    assert response.status_code == 200, response.text
    images = PdfReader(BytesIO(response.content)).pages[0].images
    assert len(images) == 1
    pixels = images[0].image.convert("RGBA").get_flattened_data()
    colour, _ = Counter(pixel[:3] for pixel in pixels if pixel[3] == 255).most_common(1)[0]
    assert colour == pytest.approx(AMBER, abs=3)


@pytest.mark.parametrize(("tenant", "template"), [(ACME, ACME_CARD), (STUDIO, STUDIO_CARD)])
async def test_a_tenant_renders_its_own_and_a_built_in_template(
    tenant_live_client, tenant, template
):
    for template_id in (template, CARD):
        response = await tenant_live_client.post(
            "/v1/render",
            headers=tenant_auth(tenant),
            json={"template": template_id, "headline": "Hallo"},
        )
        assert response.status_code == 200, f"{template_id}: {response.text}"
        assert response.headers["content-type"] == "image/png"
        assert image_of(response).size == (1080, 1350)


async def test_health_lists_no_template_of_a_tenant(tenant_live_client, tenant_app):
    response = await tenant_live_client.get("/health")
    assert response.status_code == 200
    templates = response.json()["templates"]
    assert templates == tenant_app.state.tenants.builtin_ids
    assert all(template_id.startswith(f"{BUNDLE}/") for template_id in templates)
    assert not any(name in response.text for name in tenant_app.state.tenants.names)
