import base64
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from client import AssetsClient
from config import Config
from errors import AssetsError
from tools import CarouselClosing, CarouselCover, CarouselSlide, Tools

KEY = "client-secret-key"
BUNDLE = "brand"
TEMPLATES = [
    {"id": "brand/card", "bundle": "brand", "name": "card"},
    {"id": "brand/carousel-cover", "bundle": "brand", "name": "carousel-cover"},
]


def png_bytes(size=(108, 135)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (0, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


PDF = b"%PDF-1.4 fake"


class FakeService:
    def __init__(self, fail_on: int | None = None, fail_document: bool = False):
        self.bodies: list[dict] = []
        self.documents: list[dict] = []
        self.fail_on = fail_on
        self.fail_document = fail_document

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"ok": True, "templates": TEMPLATES})
        if request.url.path == "/v1/document":
            self.documents.append(json.loads(request.content))
            if self.fail_document:
                return httpx.Response(504, json={"ok": False, "error": "Render timed out."})
            return httpx.Response(200, content=PDF, headers={"Content-Type": "application/pdf"})
        self.bodies.append(json.loads(request.content))
        if len(self.bodies) == self.fail_on:
            return httpx.Response(
                400, json={"ok": False, "error": "headline: String should have at most 140."}
            )
        return httpx.Response(
            200,
            content=png_bytes(),
            headers={"Content-Type": "image/png", "X-Request-Id": f"req-{len(self.bodies)}"},
        )


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / "Social"
    folder.mkdir()
    return folder.resolve()


@pytest.fixture
def pictures(tmp_path):
    folder = tmp_path / "Pictures"
    folder.mkdir()
    return folder.resolve()


@pytest.fixture
def service():
    return FakeService()


@pytest.fixture
def tools(service, root, pictures):
    config = Config(
        api_key=KEY,
        base_url="http://assets.test",
        timeout_s=5,
        output_root=root,
        image_roots=(root, pictures),
        bundle=BUNDLE,
    )
    return Tools(config, AssetsClient(config, transport=httpx.MockTransport(service)))


def carousel(count: int = 3) -> dict:
    return {
        "cover": CarouselCover(headline="Drei Fehler", topic="Automatisierung"),
        "slides": [
            CarouselSlide(headline=f"Punkt {number}", body=f"Text {number}")
            for number in range(1, count + 1)
        ],
        "closing": CarouselClosing(headline="Fazit", cta="Welcher Fehler passiert dir?"),
    }


async def test_list_templates_wraps_the_service_answer(tools):
    assert await tools.list_templates() == {"templates": TEMPLATES}


async def test_render_card_writes_the_png_and_summarises_it(tools, service, root):
    result = await tools.render_card(output_path="post/card.png", headline="Hallo", handle="@x")
    target = root / "post" / "card.png"
    assert target.read_bytes() == png_bytes()
    assert service.bodies == [{"headline": "Hallo", "handle": "@x"}]
    assert str(target) in result.summary
    assert "108x135" in result.summary
    assert "request req-1" in result.summary
    assert result.preview.startswith(b"\xff\xd8")


async def test_render_card_passes_template_format_and_extra_fields(tools, service):
    await tools.render_card(
        output_path="slide.png",
        headline="Kein klares Ziel",
        template="brand/carousel-content",
        format="1:1",
        extra_fields={"body": "Leg vorher fest, woran du Erfolg erkennst.", "pagination": "2/7"},
    )
    assert service.bodies == [
        {
            "template": "brand/carousel-content",
            "format": "1:1",
            "headline": "Kein klares Ziel",
            "body": "Leg vorher fest, woran du Erfolg erkennst.",
            "pagination": "2/7",
        }
    ]


@pytest.mark.parametrize("key", ["headline", "template", "image", "icon", "handle"])
async def test_extra_fields_cannot_replace_a_named_parameter(tools, service, key):
    with pytest.raises(AssetsError, match=f"must not contain '{key}'"):
        await tools.render_card(output_path="card.png", headline="Hallo", extra_fields={key: "x"})
    assert service.bodies == []


async def test_an_icon_in_extra_fields_points_at_icon_path(tools):
    with pytest.raises(AssetsError, match="Use the icon_path parameter instead"):
        await tools.render_card(
            output_path="card.png", headline="Hallo", extra_fields={"icon": "x"}
        )


async def test_render_card_sends_the_image_as_base64(tools, service, pictures):
    photo = pictures / "photo.jpg"
    photo.write_bytes(b"jpeg-bytes")
    await tools.render_card(
        output_path="card.png", headline="Hallo", template="brand/card", image_path=str(photo)
    )
    assert service.bodies[0]["image"] == base64.b64encode(b"jpeg-bytes").decode("ascii")


async def test_render_card_sends_the_icon_as_base64(tools, service, pictures):
    icon = pictures / "lightbulb.svg"
    icon.write_bytes(b"<svg/>")
    result = await tools.render_card(
        output_path="slide.png",
        headline="Hallo",
        template="brand/carousel-content",
        icon_path=str(icon),
        extra_fields={"icon_color": "text"},
    )
    assert service.bodies[0]["icon"] == base64.b64encode(b"<svg/>").decode("ascii")
    assert service.bodies[0]["icon_color"] == "text"
    assert "image" not in service.bodies[0]
    assert result.preview.startswith(b"\xff\xd8")


async def test_render_card_takes_an_image_or_an_icon_not_both(tools, service, pictures):
    (pictures / "photo.jpg").write_bytes(b"jpeg")
    (pictures / "icon.svg").write_bytes(b"<svg/>")
    with pytest.raises(AssetsError, match="^Pass either image_path or icon_path, not both.$"):
        await tools.render_card(
            output_path="card.png",
            headline="Hallo",
            image_path=str(pictures / "photo.jpg"),
            icon_path=str(pictures / "icon.svg"),
        )
    assert service.bodies == []


async def test_a_missing_icon_is_named_as_an_icon(tools, pictures):
    with pytest.raises(AssetsError, match="^The icon .*missing.svg does not exist.$"):
        await tools.render_card(
            output_path="card.png", headline="Hallo", icon_path=str(pictures / "missing.svg")
        )


async def test_bad_paths_are_refused_before_any_request(tools, service, root, tmp_path):
    (root / "taken.png").write_bytes(b"old")
    elsewhere = tmp_path / "photo.jpg"
    elsewhere.write_bytes(b"jpeg")
    calls = [
        {"output_path": "../escape.png"},
        {"output_path": "taken.png"},
        {"output_path": "card.png", "image_path": str(elsewhere)},
    ]
    for call in calls:
        with pytest.raises(AssetsError):
            await tools.render_card(headline="Hallo", **call)
    assert service.bodies == []
    assert (root / "taken.png").read_bytes() == b"old"


async def test_a_failed_render_leaves_no_file(root, pictures):
    service = FakeService(fail_on=1)
    config = Config(KEY, "http://assets.test", 5, root, (root,), BUNDLE)
    failing = Tools(config, AssetsClient(config, transport=httpx.MockTransport(service)))
    with pytest.raises(AssetsError, match="at most 140"):
        await failing.render_card(output_path="card.png", headline="x" * 141)
    assert not (root / "card.png").exists()


async def test_render_carousel_writes_numbered_slides(tools, service, root):
    result = await tools.render_carousel(
        output_dir="carousels/fehler", handle="@s", format="1:1", **carousel(3)
    )
    folder = root / "carousels" / "fehler"
    assert sorted(path.name for path in folder.iterdir()) == [
        "01.png",
        "02.png",
        "03.png",
        "04.png",
        "05.png",
    ]
    assert service.bodies == [
        {
            "template": "brand/carousel-cover",
            "headline": "Drei Fehler",
            "topic": "Automatisierung",
            "format": "1:1",
            "handle": "@s",
        },
        *[
            {
                "template": "brand/carousel-content",
                "headline": f"Punkt {number}",
                "body": f"Text {number}",
                "pagination": f"{number + 1}/5",
                "format": "1:1",
                "handle": "@s",
            }
            for number in (1, 2, 3)
        ],
        {
            "template": "brand/carousel-closing",
            "headline": "Fazit",
            "cta": "Welcher Fehler passiert dir?",
            "pagination": "5/5",
            "format": "1:1",
            "handle": "@s",
        },
    ]
    assert f"Saved 5 slides in {folder}" in result.summary
    assert "- 05.png: brand/carousel-closing, pagination 5/5" in result.summary
    assert result.preview.startswith(b"\xff\xd8")


async def test_content_slides_carry_their_graphics_into_png_and_pdf(tools, service, pictures):
    (pictures / "icon.svg").write_bytes(b"<svg/>")
    (pictures / "photo.png").write_bytes(b"png-bytes")
    slides = carousel(3)
    slides["slides"][0] = CarouselSlide(headline="Punkt 1", icon_path=str(pictures / "icon.svg"))
    slides["slides"][1] = CarouselSlide(headline="Punkt 2", image_path=str(pictures / "photo.png"))
    result = await tools.render_carousel(output_dir="c", icon_color="text", pdf=True, **slides)

    icon, image, plain = service.bodies[1:4]
    assert icon["icon"] == base64.b64encode(b"<svg/>").decode("ascii")
    assert icon["icon_color"] == "text"
    assert image["image"] == base64.b64encode(b"png-bytes").decode("ascii")
    assert "icon_color" not in image
    assert not {"icon", "image", "icon_color"} & set(plain)
    assert service.documents == [{"slides": service.bodies}]
    assert "- 02.png: brand/carousel-content, pagination 2/5, icon" in result.summary
    assert "- 03.png: brand/carousel-content, pagination 3/5, image" in result.summary


async def test_slide_graphics_are_checked_before_any_request(tools, service, pictures):
    (pictures / "icon.svg").write_bytes(b"<svg/>")
    (pictures / "photo.png").write_bytes(b"png")
    slides = carousel(2)
    slides["slides"][1] = CarouselSlide(headline="Punkt 2", icon_path="gone.svg")
    with pytest.raises(AssetsError, match="^Slide 3: The icon .*gone.svg does not exist.$"):
        await tools.render_carousel(output_dir="c", **slides)

    slides["slides"][1] = CarouselSlide(
        headline="Punkt 2", icon_path="icon.svg", image_path=str(pictures / "photo.png")
    )
    with pytest.raises(AssetsError, match="^Slide 3: Pass either image_path or icon_path"):
        await tools.render_carousel(output_dir="c", **slides)
    assert service.bodies == []


async def test_the_cover_subline_is_passed_through(tools, service):
    slides = carousel(1)
    slides["cover"] = CarouselCover(headline="Drei Fehler", subline="Und wie du sie vermeidest.")
    await tools.render_carousel(output_dir="c", **slides)
    assert service.bodies[0] == {
        "template": "brand/carousel-cover",
        "headline": "Drei Fehler",
        "subline": "Und wie du sie vermeidest.",
    }


async def test_the_pdf_holds_the_same_slides_and_lands_next_to_them(tools, service, root):
    result = await tools.render_carousel(output_dir="c", pdf=True, **carousel(2))
    assert (root / "c" / "carousel.pdf").read_bytes() == PDF
    assert service.documents == [{"slides": service.bodies}]
    assert "- carousel.pdf: all 4 slides, one page each" in result.summary


async def test_without_pdf_no_document_is_requested(tools, service, root):
    await tools.render_carousel(output_dir="c", **carousel(1))
    assert service.documents == []
    assert not (root / "c" / "carousel.pdf").exists()


async def test_an_existing_pdf_needs_overwrite(tools, service, root):
    folder = root / "c"
    folder.mkdir()
    (folder / "carousel.pdf").write_bytes(b"old")
    with pytest.raises(AssetsError, match="already holds carousel.pdf"):
        await tools.render_carousel(output_dir="c", pdf=True, **carousel(1))
    assert service.bodies == []
    assert (folder / "carousel.pdf").read_bytes() == b"old"


async def test_a_failing_pdf_saves_nothing(root):
    service = FakeService(fail_document=True)
    config = Config(KEY, "http://assets.test", 5, root, (root,), BUNDLE)
    failing = Tools(config, AssetsClient(config, transport=httpx.MockTransport(service)))
    with pytest.raises(AssetsError, match="^The PDF failed and nothing was saved: Render timed"):
        await failing.render_carousel(output_dir="c", pdf=True, **carousel(1))
    assert not (root / "c").exists() or not any((root / "c").iterdir())


async def test_carousel_pagination_follows_the_pattern(tools, service):
    await tools.render_carousel(output_dir="c", pagination="{page} von {pages}", **carousel(2))
    assert [body.get("pagination") for body in service.bodies] == [
        None,
        "2 von 4",
        "3 von 4",
        "4 von 4",
    ]


async def test_an_empty_pagination_pattern_leaves_it_out(tools, service):
    await tools.render_carousel(output_dir="c", pagination="", **carousel(1))
    assert all("pagination" not in body for body in service.bodies)


@pytest.mark.parametrize("pattern", ["{0}/{1}", "{slide}", "{page"])
async def test_an_invalid_pagination_pattern_is_refused_before_any_request(tools, service, pattern):
    with pytest.raises(AssetsError, match="placeholders"):
        await tools.render_carousel(output_dir="c", pagination=pattern, **carousel(1))
    assert service.bodies == []


async def test_a_failing_slide_saves_nothing(root, pictures):
    service = FakeService(fail_on=3)
    config = Config(KEY, "http://assets.test", 5, root, (root,), BUNDLE)
    failing = Tools(config, AssetsClient(config, transport=httpx.MockTransport(service)))
    with pytest.raises(AssetsError, match=r"^Slide 3 \(brand/carousel-content\) failed"):
        await failing.render_carousel(output_dir="c", **carousel(3))
    assert not (root / "c").exists() or not any((root / "c").iterdir())


async def test_existing_slides_need_overwrite(tools, service, root):
    folder = root / "c"
    folder.mkdir()
    (folder / "02.png").write_bytes(b"old")
    with pytest.raises(AssetsError, match="already holds 02.png"):
        await tools.render_carousel(output_dir="c", **carousel(1))
    assert service.bodies == []
    await tools.render_carousel(output_dir="c", overwrite=True, **carousel(1))
    assert (folder / "02.png").read_bytes() == png_bytes()


async def test_slides_left_from_a_longer_run_are_reported(tools, root):
    folder = root / "c"
    folder.mkdir()
    (folder / "04.png").write_bytes(b"old")
    (folder / "05.png").write_bytes(b"old")
    result = await tools.render_carousel(output_dir="c", **carousel(1))
    assert "still in the folder: 04.png, 05.png." in result.summary


async def test_a_carousel_needs_content_slides(tools, service):
    with pytest.raises(AssetsError, match="at least one content slide"):
        await tools.render_carousel(output_dir="c", **(carousel(1) | {"slides": []}))
    assert service.bodies == []


async def test_a_carousel_has_at_most_twenty_slides(tools, service):
    with pytest.raises(AssetsError, match="at most 20 slides"):
        await tools.render_carousel(output_dir="c", **carousel(19))
    assert service.bodies == []


async def test_the_carousel_needs_a_folder_of_its_own(tools, service):
    with pytest.raises(AssetsError, match="Choose a folder below"):
        await tools.render_carousel(output_dir="", **carousel(1))
    assert service.bodies == []


async def test_the_bundle_parameter_overrides_the_configured_one(tools, service):
    await tools.render_carousel(output_dir="c", bundle="other", **carousel(1))
    assert [body["template"] for body in service.bodies] == [
        "other/carousel-cover",
        "other/carousel-content",
        "other/carousel-closing",
    ]


async def test_a_carousel_without_a_bundle_names_both_ways_to_set_one(root):
    service = FakeService()
    config = Config(KEY, "http://assets.test", 5, root, (root,))
    unconfigured = Tools(config, AssetsClient(config, transport=httpx.MockTransport(service)))
    with pytest.raises(AssetsError, match="pass bundle, or set ASSETS_BUNDLE"):
        await unconfigured.render_carousel(output_dir="c", **carousel(1))
    assert service.bodies == []
    assert not (root / "c").exists()
