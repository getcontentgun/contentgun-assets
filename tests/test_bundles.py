import base64
import shutil
from pathlib import Path

import pytest
from jinja2 import TemplateNotFound

from app.formats import Format
from app.templating import TemplateRegistry, UnknownTemplateError, load_bundles
from tests.conftest import (
    BUILTIN_DIR,
    BUNDLE,
    MINIMAL_TEMPLATE,
    image_base64,
    template_toml,
    write_bundle,
    write_template,
)

IMAGE_TEMPLATE = template_toml('image = true\nfallback_image = "backdrop.png"\n')


def refusal(root: Path) -> str:
    with pytest.raises(RuntimeError) as excinfo:
        TemplateRegistry.load(root)
    return str(excinfo.value)


def render(registry: TemplateRegistry, template_id: str) -> str:
    spec = registry.get(template_id)
    return registry.render_html(spec, Format.PORTRAIT_4_5, spec.samples)


def write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(image_base64()))


def test_the_bundle_of_the_suite_loads():
    registry = TemplateRegistry.load(BUILTIN_DIR)
    assert BUNDLE in [bundle.name for bundle in registry.bundles]
    assert all(spec.id == f"{spec.bundle}/{spec.name}" for spec in registry.specs)


def test_a_template_is_addressed_by_bundle_and_name(tmp_path):
    write_bundle(tmp_path)
    registry = TemplateRegistry.load(tmp_path)
    assert registry.ids == ["mini/plain"]
    spec = registry.get("mini/plain")
    assert (spec.bundle, spec.name) == ("mini", "plain")
    with pytest.raises(UnknownTemplateError):
        registry.get("plain")


def test_bundles_and_templates_are_listed_in_order(tmp_path):
    write_bundle(
        tmp_path, "zeta", templates={"second": MINIMAL_TEMPLATE, "first": MINIMAL_TEMPLATE}
    )
    write_bundle(tmp_path, "alpha")
    registry = TemplateRegistry.load(tmp_path)
    assert [bundle.name for bundle in registry.bundles] == ["alpha", "zeta"]
    assert registry.ids == ["alpha/plain", "zeta/first", "zeta/second"]


def test_the_bundle_description_is_loaded(tmp_path):
    write_bundle(tmp_path)
    assert TemplateRegistry.load(tmp_path).bundle("mini").description == (
        "A bundle of the test suite."
    )


def test_shared_folders_and_files_are_neither_bundles_nor_templates(tmp_path):
    bundle = write_bundle(tmp_path)
    for skipped in ("_shared", ".git"):
        (tmp_path / skipped).mkdir()
    (tmp_path / "README.md").write_text("Bundles", encoding="utf-8")
    for skipped in ("_base", ".cache", "assets"):
        (bundle / skipped).mkdir()
    (bundle / "README.md").write_text("Brand values", encoding="utf-8")
    assert TemplateRegistry.load(tmp_path).ids == ["mini/plain"]


def test_two_registries_on_two_directories_stay_apart(tmp_path):
    write_bundle(tmp_path / "one", "first", family="First Sans")
    write_bundle(tmp_path / "two", "second", family="Second Sans")
    one = TemplateRegistry.load(tmp_path / "one")
    two = TemplateRegistry.load(tmp_path / "two")
    assert one.ids == ["first/plain"]
    assert two.ids == ["second/plain"]
    with pytest.raises(UnknownTemplateError):
        one.get("second/plain")
    assert one.font_checks(one.get("first/plain"))[0].endswith('"First Sans"')
    assert two.font_checks(two.get("second/plain"))[0].endswith('"Second Sans"')


def test_a_page_embeds_only_the_fonts_of_its_own_bundle(tmp_path):
    for name, family in (("first", "First Sans"), ("second", "Second Sans")):
        write_bundle(tmp_path, name, family=family)
        write_template(tmp_path / name, "page", MINIMAL_TEMPLATE, html="{{ font_css }}")
    registry = TemplateRegistry.load(tmp_path)
    html = render(registry, "first/page")
    assert html.count("@font-face") == 1
    assert '"First Sans"' in html
    assert "Second Sans" not in html
    assert registry.font_checks(registry.get("first/page"))[0].endswith('"First Sans"')


@pytest.mark.parametrize(
    "html",
    [
        '{% include "../second/secret.html" %}',
        '{% extends "../second/secret.html" %}',
        '{% include "/second/secret.html" %}',
    ],
    ids=["include", "extends", "absolute"],
)
def test_a_template_cannot_reach_into_another_bundle(tmp_path, html):
    write_bundle(tmp_path, "first", templates={})
    write_template(tmp_path / "first", "page", MINIMAL_TEMPLATE, html=html)
    write_bundle(tmp_path, "second")
    (tmp_path / "second" / "secret.html").write_text("secret", encoding="utf-8")
    registry = TemplateRegistry.load(tmp_path)
    with pytest.raises(TemplateNotFound):
        render(registry, "first/page")


def test_a_link_out_of_the_bundle_is_not_followed(tmp_path):
    write_bundle(tmp_path, "second")
    (tmp_path / "second" / "_shared").mkdir()
    (tmp_path / "second" / "_shared" / "secret.html").write_text("secret", encoding="utf-8")
    first = write_bundle(tmp_path, "first", templates={})
    write_template(first, "page", MINIMAL_TEMPLATE, html='{% include "_shared/secret.html" %}')
    (first / "_shared").symlink_to(tmp_path / "second" / "_shared")
    registry = TemplateRegistry.load(tmp_path)
    with pytest.raises(TemplateNotFound):
        render(registry, "first/page")


def test_shared_files_inside_the_bundle_are_reachable(tmp_path):
    bundle = write_bundle(tmp_path, templates={})
    (bundle / "_base").mkdir()
    (bundle / "_base" / "base.html.j2").write_text("base {% block body %}{% endblock %}")
    write_template(
        bundle,
        "page",
        MINIMAL_TEMPLATE,
        html='{% extends "_base/base.html.j2" %}{% block body %}page{% endblock %}',
    )
    assert render(TemplateRegistry.load(tmp_path), "mini/page") == "base page"


def test_inline_svg_reads_the_assets_of_its_own_bundle_only(tmp_path):
    first = write_bundle(tmp_path, "first", templates={})
    (first / "assets").mkdir()
    (first / "assets" / "logo.svg").write_text("<svg>own</svg>", encoding="utf-8")
    write_template(first, "own", MINIMAL_TEMPLATE, html='{{ inline_svg("logo.svg") }}')
    write_template(
        first, "foreign", MINIMAL_TEMPLATE, html='{{ inline_svg("../../second/assets/logo.svg") }}'
    )
    second = write_bundle(tmp_path, "second")
    (second / "assets").mkdir()
    (second / "assets" / "logo.svg").write_text("<svg>foreign</svg>", encoding="utf-8")
    registry = TemplateRegistry.load(tmp_path)
    assert render(registry, "first/own") == "<svg>own</svg>"
    with pytest.raises(RuntimeError, match="Unknown asset"):
        render(registry, "first/foreign")


def test_the_fallback_image_comes_from_the_own_assets(tmp_path):
    bundle = write_bundle(tmp_path, templates={"photo": IMAGE_TEMPLATE})
    write_png(bundle / "assets" / "backdrop.png")
    spec = TemplateRegistry.load(tmp_path).get("mini/photo")
    assert spec.fallback_image == bundle.resolve() / "assets" / "backdrop.png"


def test_a_fallback_image_of_another_bundle_is_refused(tmp_path):
    toml = template_toml('image = true\nfallback_image = "../../second/assets/backdrop.png"\n')
    write_bundle(tmp_path, "first", templates={"photo": toml})
    write_bundle(tmp_path, "second")
    write_png(tmp_path / "second" / "assets" / "backdrop.png")
    text = refusal(tmp_path)
    assert "Template 'first/photo' declares the fallback image" in text
    assert str(tmp_path / "first" / "photo" / "template.toml") in text


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "declares the fallback image 'backdrop.png', which is not in"),
        (b"GIF89a" + b"x" * 64, "which is not a PNG, JPEG or WebP file"),
    ],
    ids=["missing", "wrong format"],
)
def test_an_unusable_fallback_image_stops_the_start(tmp_path, content, message):
    bundle = write_bundle(tmp_path, templates={"photo": IMAGE_TEMPLATE})
    if content is not None:
        (bundle / "assets").mkdir()
        (bundle / "assets" / "backdrop.png").write_bytes(content)
    text = refusal(tmp_path)
    assert message in text
    assert text.startswith(f"{bundle.resolve() / 'photo' / 'template.toml'}: ")


def test_a_missing_bundle_directory_stops_the_start(tmp_path):
    assert refusal(tmp_path / "nowhere") == (
        f"{tmp_path / 'nowhere'}: The bundle directory does not exist."
    )


def test_a_directory_without_bundles_stops_the_start(tmp_path):
    (tmp_path / "_shared").mkdir()
    assert refusal(tmp_path) == f"{tmp_path}: No bundles found."


def test_loading_the_bundles_of_an_empty_directory_gives_none(tmp_path):
    (tmp_path / "_shared").mkdir()
    assert load_bundles(tmp_path) == {}


def test_two_registries_can_share_a_bundle(tmp_path):
    write_bundle(tmp_path)
    bundles = load_bundles(tmp_path)
    one, two = TemplateRegistry(bundles, "mini/plain"), TemplateRegistry(dict(bundles))
    assert one.bundle("mini") is two.bundle("mini")
    assert (one.default_template, two.default_template) == ("mini/plain", None)


def test_a_bundle_without_templates_stops_the_start(tmp_path):
    bundle = write_bundle(tmp_path, templates={})
    (bundle / "assets").mkdir()
    assert refusal(tmp_path) == f"{bundle}: Bundle 'mini' has no templates."


def test_a_bundle_without_bundle_toml_stops_the_start(tmp_path):
    bundle = write_bundle(tmp_path)
    (bundle / "bundle.toml").unlink()
    assert refusal(tmp_path) == f"{bundle}: Bundle 'mini' has no bundle.toml."


@pytest.mark.parametrize("name", ["Mini", "mini_bundle", "-mini", "mini bundle"])
def test_a_bundle_name_outside_the_rule_stops_the_start(tmp_path, name):
    write_bundle(tmp_path, name)
    assert f"The bundle name {name!r} must use lowercase letters" in refusal(tmp_path)


@pytest.mark.parametrize(
    ("replace", "message"),
    [
        (('name = "mini"', 'name = "other"'), "names itself 'other', which does not match"),
        (('name = "mini"', 'name = "mini"\nversion = 2'), "has the unknown key 'version'"),
        (('name = "mini"', 'name = "mini'), "is not valid TOML"),
        (
            ('description = "A bundle of the test suite."', "description = 1"),
            "has a description that is not a string",
        ),
    ],
    ids=["other name", "unknown key", "broken toml", "description"],
)
def test_an_invalid_bundle_toml_stops_the_start(tmp_path, replace, message):
    bundle = write_bundle(tmp_path)
    path = bundle / "bundle.toml"
    path.write_text(path.read_text(encoding="utf-8").replace(*replace), encoding="utf-8")
    text = refusal(tmp_path)
    assert text.startswith(f"{bundle.resolve() / 'bundle.toml'}: Bundle 'mini' ")
    assert message in text


def test_a_broken_font_file_stops_the_start(tmp_path):
    bundle = write_bundle(tmp_path)
    (bundle / "fonts" / "face.woff2").write_bytes(b"wOF2")
    text = refusal(tmp_path)
    assert "Font files of bundle 'mini' are unusable: face.woff2 is only 4 bytes" in text
    assert str(bundle.resolve() / "fonts") in text


def test_a_font_check_without_a_face_stops_the_start(tmp_path):
    bundle = write_bundle(tmp_path)
    path = bundle / "bundle.toml"
    path.write_text(path.read_text().replace('20px "Mini Sans"', '20px "Other Sans"'))
    assert "but no font face of 'Other Sans' covers the weight" in refusal(tmp_path)


@pytest.mark.parametrize("name", ["Plain", "plain_card", "-plain"])
def test_a_template_name_outside_the_rule_stops_the_start(tmp_path, name):
    write_bundle(tmp_path, templates={name: MINIMAL_TEMPLATE})
    assert f"The template name {name!r} must use lowercase letters" in refusal(tmp_path)


@pytest.mark.parametrize("missing", ["template.toml", "template.html.j2"])
def test_a_template_without_its_files_stops_the_start(tmp_path, missing):
    bundle = write_bundle(tmp_path)
    (bundle / "plain" / missing).unlink()
    text = refusal(tmp_path)
    assert text == f"{bundle.resolve() / 'plain'}: Template 'mini/plain' has no {missing}."


def test_a_template_linked_from_another_bundle_stops_the_start(tmp_path):
    write_bundle(tmp_path, "second")
    first = write_bundle(tmp_path, "first", templates={})
    (first / "plain").symlink_to(tmp_path / "second" / "plain")
    assert "Template 'first/plain' lies outside its bundle." in refusal(tmp_path)


def test_a_template_file_linked_from_outside_stops_the_start(tmp_path):
    bundle = write_bundle(tmp_path)
    outside = tmp_path / "elsewhere.css"
    outside.write_text("body {}", encoding="utf-8")
    (bundle / "plain" / "template.css").symlink_to(outside)
    assert "Template 'mini/plain' reaches outside its bundle." in refusal(tmp_path)


def test_a_bundle_linked_into_the_directory_is_loaded(tmp_path):
    write_bundle(tmp_path / "elsewhere")
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "mini").symlink_to(tmp_path / "elsewhere" / "mini")
    assert TemplateRegistry.load(tmp_path / "root").ids == ["mini/plain"]


def test_the_suite_bundle_carries_everything_it_needs(tmp_path):
    shutil.copytree(BUILTIN_DIR / BUNDLE, tmp_path / BUNDLE)
    registry = TemplateRegistry.load(tmp_path)
    assert registry.ids == [spec.id for spec in TemplateRegistry.load(BUILTIN_DIR).specs]
    for spec in registry.specs:
        fields = dict.fromkeys(spec.fields, "") | spec.defaults | spec.samples
        assert registry.render_html(spec, spec.formats[0], fields)
