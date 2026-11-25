import shutil
from pathlib import Path

import pytest

from app.errors import ApiError
from app.images import icon_file_data_uri
from app.main import create_app
from app.rendering import check_render
from app.schemas import RenderRequest
from app.templating import TemplateRegistry
from tests.conftest import (
    AUTH,
    BUILTIN_DIR,
    BUNDLE,
    CARD,
    CONTENT,
    SQUARE_SVG,
    client_of,
    image_base64,
    svg_base64,
    template_toml,
    tenants_for,
    write_bundle,
)

ICON_TEMPLATE = template_toml("icon = true\n")
UPLOAD_ID = "up_" + "a" * 24
ICON_TAG = 'class="graphic graphic-icon"'


def icon_svg(width: int) -> str:
    return SQUARE_SVG.replace('<rect width="16"', f'<rect width="{width}"')


ICONS = {"rocket.svg": icon_svg(4), "spark.svg": icon_svg(8), "arrow.svg": icon_svg(12)}


def write_icons(bundle_dir: Path, files: dict[str, str]) -> None:
    folder = bundle_dir / "assets" / "icons"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (folder / name).write_text(content, encoding="utf-8")


@pytest.fixture(scope="module")
def icon_root(tmp_path_factory) -> Path:
    """A copy of the bundle the suite tests, with a small icon set of its own."""
    root = tmp_path_factory.mktemp("bundles")
    shutil.copytree(BUILTIN_DIR / BUNDLE, root / BUNDLE)
    write_icons(root / BUNDLE, ICONS)
    return root


@pytest.fixture(scope="module")
def registry(icon_root):
    return TemplateRegistry.load(icon_root, default_template=CARD)


@pytest.fixture
async def icon_client(icon_root):
    async with client_of(create_app(tenants_for(builtin_dir=icon_root))) as instance:
        yield instance


def refusal(root: Path) -> str:
    with pytest.raises(RuntimeError) as excinfo:
        TemplateRegistry.load(root)
    return str(excinfo.value)


def checked(registry: TemplateRegistry, **payload):
    return check_render(registry, RenderRequest(headline="Hallo", **payload))


def refused(registry: TemplateRegistry, **payload) -> str:
    with pytest.raises(ApiError) as excinfo:
        checked(registry, **payload)
    assert excinfo.value.status_code == 400
    return excinfo.value.message


def test_the_bundle_of_the_suite_has_no_icon_set():
    registry = TemplateRegistry.load(BUILTIN_DIR)
    assert registry.icons(registry.get(CONTENT)) == {}


def test_an_icon_set_is_named_by_its_files(tmp_path):
    bundle_dir = write_bundle(
        tmp_path, templates={"slide": ICON_TEMPLATE, "plain": template_toml()}
    )
    write_icons(
        bundle_dir, {"spark.svg": SQUARE_SVG, "two-1.svg": SQUARE_SVG, "LICENSE.txt": "CC0"}
    )
    registry = TemplateRegistry.load(tmp_path)
    assert sorted(registry.icons(registry.get("mini/slide"))) == ["spark", "two-1"]
    assert registry.icons(registry.get("mini/plain")) == {}


def test_an_icon_name_outside_the_rule_stops_the_start(tmp_path):
    write_icons(
        write_bundle(tmp_path, templates={"slide": ICON_TEMPLATE}), {"Spark.svg": SQUARE_SVG}
    )
    assert "The icon name 'Spark' must use lowercase letters" in refusal(tmp_path)


def test_an_icon_against_the_svg_rules_stops_the_start(tmp_path):
    markup = SQUARE_SVG.replace("<rect", "<text>A</text><rect")
    write_icons(write_bundle(tmp_path, templates={"slide": ICON_TEMPLATE}), {"spark.svg": markup})
    message = refusal(tmp_path)
    assert "spark.svg" in message
    assert "Icon 'spark' must not contain text." in message


def test_an_icon_name_picks_the_icon_of_the_set(registry):
    path = registry.icons(registry.get(CONTENT))["rocket"]
    card = checked(registry, template=CONTENT, icon_name=" rocket ")
    assert card.icon == icon_file_data_uri(path)


def test_an_icon_stays_optional(registry):
    card = checked(registry, template=CONTENT)
    assert (card.image, card.icon) == (None, None)


def test_an_unknown_icon_name_suggests_close_ones(registry):
    message = refused(registry, template=CONTENT, icon_name="rockets")
    assert message == f"Unknown icon 'rockets' for template {CONTENT!r}. Close: rocket."


def test_an_unknown_icon_name_without_close_ones_points_at_the_listing(registry):
    message = refused(registry, template=CONTENT, icon_name="zzzzzzzz")
    assert message.endswith("GET /v1/templates lists them.")


def test_an_icon_name_for_a_template_without_icons_raises_400(registry):
    assert refused(registry, template=CARD, icon_name="rocket") == (
        f"Template {CARD!r} does not accept an icon."
    )


@pytest.mark.parametrize(
    "other", [{"icon": svg_base64()}, {"icon_upload": UPLOAD_ID}], ids=["icon", "icon upload"]
)
def test_an_icon_name_next_to_another_icon_raises_400(registry, other):
    assert refused(registry, template=CONTENT, icon_name="rocket", **other) == (
        "Send the icon in one field only: icon, icon_upload, icon_url or icon_name."
    )


def test_an_icon_name_next_to_an_image_raises_400(registry):
    message = refused(registry, template=CONTENT, icon_name="rocket", image=image_base64())
    assert message == "Send either an image or an icon, not both."


def test_a_template_whose_bundle_has_no_set_takes_no_icon_name(tmp_path):
    write_bundle(tmp_path, templates={"slide": ICON_TEMPLATE})
    message = refused(TemplateRegistry.load(tmp_path), template="mini/slide", icon_name="spark")
    assert message == (
        "Template 'mini/slide' has no icons to name. Send the SVG as icon, icon_upload or icon_url."
    )


async def test_the_listing_names_the_icons_of_a_template(icon_client):
    body = (await icon_client.get("/v1/templates", headers=AUTH)).json()
    icons = {entry["id"]: entry["icons"] for entry in body["templates"]}
    assert icons[CONTENT] == ["arrow", "rocket", "spark"]
    assert icons[CARD] == []


async def test_the_debug_route_takes_an_icon_name(icon_client, registry):
    params = {"template": CONTENT}
    assert ICON_TAG not in (await icon_client.get("/v1/debug/html", params=params)).text
    response = await icon_client.get("/v1/debug/html", params=params | {"icon_name": "rocket"})
    path = registry.icons(registry.get(CONTENT))["rocket"]
    assert ICON_TAG in response.text
    assert icon_file_data_uri(path) in response.text


async def test_the_debug_route_refuses_an_icon_name_next_to_an_icon_url(icon_client):
    response = await icon_client.get(
        "/v1/debug/html",
        params={
            "template": CONTENT,
            "icon_name": "rocket",
            "icon_url": "/v1/debug/samples/icon.svg",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "Send the icon in one field only: icon_url or icon_name."


async def test_the_debug_icons_page_shows_a_set(icon_client):
    response = await icon_client.get("/v1/debug/icons", params={"bundle": BUNDLE})
    assert response.status_code == 200
    assert "<figcaption>rocket</figcaption>" in response.text
    assert response.text.count("data:image/svg+xml;base64,") == len(ICONS)


async def test_the_debug_icons_page_needs_a_bundle(icon_client):
    response = await icon_client.get("/v1/debug/icons", params={"bundle": "nope"})
    assert response.status_code == 400
    assert response.json()["error"].startswith("Name a bundle with the parameter bundle.")


async def test_the_debug_overview_links_the_icons_of_a_bundle_with_a_set(icon_client, client):
    assert f"/v1/debug/icons?bundle={BUNDLE}" in (await icon_client.get("/v1/debug")).text
    assert "/v1/debug/icons" not in (await client.get("/v1/debug")).text
