import json
import re

import pytest

from app.formats import Format
from app.hyphenation import SOFT_HYPHEN
from app.templating import (
    TemplateRegistry,
    UnknownTemplateError,
    UnsupportedFormatError,
    _load_spec,
    highlight,
    percent,
    registered,
)
from tests.conftest import (
    BUILTIN_DIR,
    BUNDLE,
    CARD,
    CARD_IMAGE,
    CAROUSEL,
    CLOSING,
    CONTENT,
    COVER,
    DISPLAY_FACE,
    QUOTE,
    SHOWCASE,
    TEXT_FACE,
    template_toml,
    write_template,
)

FIELDS = {
    "headline": "Gute Vorlagen sparen jede Woche Stunden.",
    "subline": "Text rein, fertige Karte raus.",
    "handle": "@contentgun",
}

SECONDARY = {COVER: "topic", CONTENT: "body", CLOSING: "cta"}


@pytest.fixture(scope="module")
def registry():
    return TemplateRegistry.load(BUILTIN_DIR)


def sample_html(registry, template_id: str, fmt: Format = Format.PORTRAIT_4_5, **fields) -> str:
    spec = registry.get(template_id)
    return registry.render_html(spec, fmt, spec.defaults | spec.samples | fields)


def load_spec(tmp_path, toml: str):
    return _load_spec(write_template(tmp_path, "plain", toml), "mini", tmp_path)


def test_registry_finds_every_template(registry):
    assert registry.ids == [CARD, CARD_IMAGE, CLOSING, CONTENT, COVER, QUOTE]


def test_shared_partials_are_not_a_template(registry):
    assert f"{BUNDLE}/_base" not in registry.ids
    with pytest.raises(UnknownTemplateError):
        registry.get(f"{BUNDLE}/_base")


def test_samples_are_loaded_as_strings(tmp_path):
    spec = load_spec(tmp_path, template_toml(samples='[samples]\nheadline = "Hallo"\nnote = 2\n'))
    assert spec.samples == {"headline": "Hallo", "note": "2"}


def test_sample_for_an_undeclared_field_raises(tmp_path):
    toml = template_toml(samples='[samples]\nheadline = "Hallo"\nsubline = "x"\n')
    with pytest.raises(RuntimeError) as excinfo:
        load_spec(tmp_path, toml)
    assert "has samples for undeclared fields: ['subline']" in str(excinfo.value)
    assert str(excinfo.value).startswith(f"{tmp_path / 'plain' / 'template.toml'}: ")


@pytest.mark.parametrize(
    "samples",
    ['note = "2"\n', 'headline = "   "\n', 'note = "2"\n[defaults]\nheadline = "Hallo"\n'],
    ids=["missing", "blank", "default only"],
)
def test_required_field_without_a_sample_raises(tmp_path, samples):
    with pytest.raises(RuntimeError) as excinfo:
        load_spec(tmp_path, template_toml(samples="[samples]\n" + samples))
    assert "has no sample for the required field 'headline'" in str(excinfo.value)


def test_image_and_icon_capabilities(registry):
    images = {spec.id for spec in registry.specs if spec.accepts_image}
    icons = {spec.id for spec in registry.specs if spec.accepts_icon}
    assert images == {CARD_IMAGE, CONTENT, QUOTE}
    assert icons == {CONTENT}


def test_card_image_renders_the_backdrop_and_scrim(registry):
    html = registry.render_html(
        registry.get(CARD_IMAGE),
        Format.PORTRAIT_4_5,
        FIELDS,
        image="data:image/png;base64,AAAA",
    )
    assert '<img class="backdrop"' in html
    assert 'src="data:image/png;base64,AAAA"' in html
    assert '<div class="scrim">' in html


def test_card_image_declares_its_fallback_from_the_bundle_assets(registry):
    spec = registry.get(CARD_IMAGE)
    assert spec.fallback_image.parent == (BUILTIN_DIR / BUNDLE / "assets").resolve()
    assert spec.fallback_image.is_file()
    assert registry.get(CARD).fallback_image is None


def test_render_html_without_an_image_omits_the_backdrop(registry):
    html = registry.render_html(registry.get(CARD_IMAGE), Format.PORTRAIT_4_5, FIELDS)
    assert "<img" not in html
    assert '<div class="scrim">' not in html


ICON_URI = "data:image/svg+xml;base64,AAAA"


def content_html(registry, image: str | None = None, icon: str | None = None, **fields) -> str:
    spec = registry.get(CONTENT)
    return registry.render_html(
        spec, Format.PORTRAIT_4_5, spec.defaults | spec.samples | fields, image=image, icon=icon
    )


def test_carousel_content_without_a_graphic_draws_none(registry):
    html = content_html(registry)
    assert 'class="graphic' not in html
    assert 'id="tint"' not in html


def test_carousel_content_shows_an_image_untinted(registry):
    html = content_html(registry, image="data:image/png;base64,AAAA")
    assert (
        '<img class="graphic graphic-image" id="graphic" src="data:image/png;base64,AAAA"' in html
    )
    assert 'id="tint"' not in html


@pytest.mark.parametrize("colour", ["accent", "text"])
def test_carousel_content_tints_an_icon(registry, colour):
    html = content_html(registry, icon=ICON_URI, icon_color=colour)
    assert f'<img class="graphic graphic-icon" id="graphic" src="{ICON_URI}"' in html
    assert f'<svg class="tint" data-color="{colour}"' in html
    assert '<filter id="tint" color-interpolation-filters="sRGB">' in html


def test_the_graphic_follows_the_body(registry):
    html = content_html(registry, icon=ICON_URI)
    assert html.index('id="body"') < html.index('id="graphic"')


def test_unknown_template_raises(registry):
    with pytest.raises(UnknownTemplateError):
        registry.get("nope")


def test_a_template_name_without_its_bundle_is_unknown(registry):
    with pytest.raises(UnknownTemplateError):
        registry.get(CARD.split("/")[1])


def test_render_contains_all_fields(registry):
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, FIELDS)
    assert "Gute Vorlagen sparen jede Woche Stunden." in html
    assert "Text rein, fertige Karte raus." in html
    assert "@contentgun" in html


def test_render_sets_viewport_variables(registry):
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, FIELDS)
    assert "--w: 1080px" in html
    assert "--h: 1350px" in html


def test_render_embeds_the_fonts_of_the_bundle_as_data_uris(registry):
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, FIELDS)
    faces = registry.bundle(BUNDLE).fonts.faces
    assert html.count("data:font/woff2;base64,") == len(faces)
    for face in faces:
        assert f'font-family: "{face.family}";' in html


def test_registered_escapes_plain_text():
    assert registered("<b>Brand®</b>") == (
        '&lt;b&gt;Brand<sup class="registered">®</sup>&lt;/b&gt;'
    )


def test_css_is_not_html_escaped(registry):
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, FIELDS)
    assert "&#34;" not in html
    assert f'font-family: "{DISPLAY_FACE[0]}"' in css_rule(html, ".headline")
    assert "@font-face" in html


def test_font_check_list_is_valid_json(registry):
    spec = registry.get(CARD)
    html = registry.render_html(spec, Format.PORTRAIT_4_5, FIELDS)
    match = re.search(r"var FACES = (\[.*?\]);", html, re.S)
    assert match is not None
    assert json.loads(match.group(1)) == list(registry.font_checks(spec))


def css_rule(html: str, selector: str) -> str:
    return html.split(selector + " {")[1].split("}")[0]


def test_headline_uses_the_display_font(registry):
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, FIELDS)
    rule = css_rule(html, ".headline")
    assert f'font-family: "{DISPLAY_FACE[0]}"' in rule
    assert f"font-weight: {DISPLAY_FACE[1]}" in rule


def test_subline_uses_the_body_font(registry):
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, FIELDS)
    assert 'class="copy subline"' in html
    assert_face(css_rules(html, ".copy"), *TEXT_FACE)
    assert_inherits_face(css_rules(html, ".subline"))


def test_meta_rows_use_the_body_font(registry):
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, FIELDS)
    assert f'font-family: "{TEXT_FACE[0]}"' in css_rule(html, ".meta")


def test_handle_sits_in_the_header_above_the_headline(registry):
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, FIELDS)
    assert (
        html.index('<div class="meta">') < html.index("@contentgun") < html.index('id="headline"')
    )


def test_empty_handle_removes_the_element(registry):
    fields = dict(FIELDS, handle="")
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, fields)
    assert 'class="handle"' not in html


def test_text_block_is_left_aligned(registry):
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, FIELDS)
    rule = css_rule(html, ".stage")
    assert "text-align: left" in rule
    assert "align-items: flex-start" in rule
    assert "justify-content: flex-start" in css_rule(html, ".fit-box")


def test_render_escapes_markup(registry):
    fields = dict(FIELDS, headline="<script>alert(1)</script>")
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, fields)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_empty_subline_removes_the_element(registry):
    fields = dict(FIELDS, subline="")
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, fields)
    assert 'id="subline"' not in html


def test_unsupported_format_raises(registry):
    with pytest.raises(UnsupportedFormatError):
        registry.render_html(registry.get(CARD), Format.SQUARE, FIELDS)


def test_fit_text_flag_reaches_the_page(registry):
    spec = registry.get(CARD)
    assert "var FIT = false;" in registry.render_html(
        spec, Format.PORTRAIT_4_5, FIELDS, fit_text=False
    )
    assert "var FIT = true;" in registry.render_html(spec, Format.PORTRAIT_4_5, FIELDS)


def test_the_headline_carries_soft_hyphens(registry):
    fields = FIELDS | {"headline": "Unternehmensberatungsgesellschaft"}
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, fields)
    assert SOFT_HYPHEN in html


def test_the_handle_carries_none(registry):
    fields = FIELDS | {"handle": "@unternehmensberatungsgesellschaft"}
    html = registry.render_html(registry.get(CARD), Format.PORTRAIT_4_5, fields)
    handle = html.split('class="handle">')[1].split("</span>")[0]
    assert handle == "@unternehmensberatungsgesellschaft"


@pytest.mark.parametrize("name", [CONTENT, CLOSING])
@pytest.mark.parametrize("pagination", ["3/7", "3 von 7", "3", "Seitennummerierung"])
def test_pagination_is_shown_exactly_as_sent(registry, name, pagination):
    html = sample_html(registry, name, pagination=pagination)
    assert f'<span class="pagination">{pagination}</span>' in html


@pytest.mark.parametrize("name", [CONTENT, CLOSING])
def test_empty_pagination_removes_the_element(registry, name):
    assert 'class="pagination"' not in sample_html(registry, name, pagination="")


def test_pagination_is_escaped(registry):
    html = sample_html(registry, CONTENT, pagination="<b>3</b>")
    assert '<span class="pagination">&lt;b&gt;3&lt;/b&gt;</span>' in html


def test_only_the_cover_draws_the_swipe_arrow(registry):
    assert 'class="arrow"' in sample_html(registry, COVER)
    assert 'class="arrow"' not in sample_html(registry, CONTENT)
    assert 'class="arrow"' not in sample_html(registry, CLOSING)


def test_the_cover_has_no_pagination(registry):
    assert "pagination" not in registry.get(COVER).fields


def css_rules(html: str, selector: str) -> str:
    return "".join(part.split("}")[0] for part in html.split(selector + " {")[1:])


def assert_face(rules: str, family: str, weight: int) -> None:
    assert set(re.findall(r'font-family: ("[^"]+")', rules)) == {f'"{family}"'}
    assert set(re.findall(r"font-weight: (\d+)", rules)) == {str(weight)}


def assert_inherits_face(rules: str) -> None:
    assert "font-family" not in rules
    assert "font-weight" not in rules


@pytest.mark.parametrize("name", CAROUSEL)
def test_carousel_headline_uses_the_display_font(registry, name):
    assert_face(css_rules(sample_html(registry, name), ".headline"), *DISPLAY_FACE)


@pytest.mark.parametrize(
    ("name", "field"), [(COVER, "subline"), (CONTENT, "body"), (CLOSING, "cta")]
)
def test_carousel_copy_uses_the_body_font(registry, name, field):
    # The font checks probe the text face only, so a face set on the field itself would go
    # unchecked.
    html = sample_html(registry, name)
    assert f'class="copy {field}"' in html
    assert_face(css_rules(html, ".copy"), *TEXT_FACE)
    assert_inherits_face(css_rules(html, f".{field}"))


def test_cover_shows_the_subline_under_the_headline(registry):
    html = sample_html(registry, COVER, subline="Und wie du sie vermeidest.")
    assert html.index('id="headline"') < html.index('id="subline"')
    assert 'id="subline"' not in sample_html(registry, COVER, subline="")


def test_cover_puts_the_topic_in_the_place_of_the_bar(registry):
    with_topic = sample_html(registry, COVER, topic="Arbeitsweise")
    assert with_topic.index('id="topic"') < with_topic.index('id="headline"')
    assert 'class="bar"' not in with_topic
    without = sample_html(registry, COVER, topic="")
    assert 'id="topic"' not in without
    assert without.index('class="bar"') < without.index('id="headline"')


@pytest.mark.parametrize("name", [CARD, CONTENT, CLOSING])
def test_the_accent_bar_stands_above_the_headline(registry, name):
    html = sample_html(registry, name)
    assert html.index('class="bar"') < html.index('id="headline"')


def test_cover_topic_uses_the_body_font(registry):
    assert_face(css_rules(sample_html(registry, COVER), ".topic"), *TEXT_FACE)


@pytest.mark.parametrize("name", CAROUSEL)
def test_carousel_meta_row_uses_the_body_font(registry, name):
    html = sample_html(registry, name)
    assert_face(css_rules(html, ".meta"), *TEXT_FACE)
    assert_inherits_face(css_rules(html, ".handle"))
    assert_inherits_face(css_rules(html, ".pagination"))


@pytest.mark.parametrize("name", CAROUSEL)
def test_every_fitted_element_has_a_size_range(registry, name):
    html = sample_html(registry, name)
    fitted = re.findall(r'data-fit="([^"]+)"', html)
    assert fitted
    for fit in fitted:
        assert f"--{fit}-min:" in html, fit
        assert f"--{fit}-max:" in html, fit


@pytest.mark.parametrize(("name", "field"), [*SECONDARY.items(), (COVER, "subline")])
def test_carousel_copy_carries_soft_hyphens(registry, name, field):
    html = sample_html(registry, name, **{field: "Unternehmensberatungsgesellschaft"})
    element = html.split(f'id="{field}"')[1].split("</p>")[0]
    assert SOFT_HYPHEN in element


@pytest.mark.parametrize("name", CAROUSEL)
def test_carousel_meta_row_carries_none(registry, name):
    html = sample_html(registry, name, handle="@unternehmensberatungsgesellschaft")
    meta = html.split('<div class="meta">')[1].split("</div>")[0]
    assert "@unternehmensberatungsgesellschaft" in meta
    assert SOFT_HYPHEN not in meta


@pytest.mark.parametrize("name", CAROUSEL)
def test_carousel_templates_render_portrait_and_square(registry, name):
    assert registry.get(name).formats == (Format.PORTRAIT_4_5, Format.SQUARE)
    html = sample_html(registry, name, Format.SQUARE)
    assert "--w: 1080px; --h: 1080px;" in html
    assert 'data-format="1:1"' in html


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ("[limits]\nsubline = 10\n", "limits undeclared fields: ['subline']"),
        ("[limits]\nnote = 0\n", "limits 'note' to 0"),
        ("[limits]\nnote = 201\n", "limits 'note' to 201"),
        ('[limits]\nnote = "10"\n', "limits 'note' to '10'"),
        ("[limits]\nheadline = 3\n", "sample for 'headline' that exceeds its limit"),
        (
            'image = true\nicon = true\nfallback_image = "backdrop.jpg"\n',
            "declares a fallback image and icon = true",
        ),
    ],
    ids=["undeclared", "zero", "above the cap", "string", "sample too long", "fallback with icon"],
)
def test_invalid_template_settings_raise(tmp_path, toml, message):
    with pytest.raises(RuntimeError) as excinfo:
        load_spec(tmp_path, template_toml(toml))
    assert message in str(excinfo.value)


def test_highlight_marks_the_first_whole_word_ignoring_case():
    assert highlight("KI für\nCapos", "capos") == "KI für\n<em>Capos</em>"
    assert highlight("Capos und Capos", "Capos") == "<em>Capos</em> und Capos"


def test_highlight_needs_a_whole_word():
    assert highlight("Kapitalisten", "Kapital") == highlight("Kapitalisten", "")
    assert "<em>" not in highlight("Kapitalisten", "Kapital")


def test_highlight_without_a_match_still_hyphenates_and_escapes():
    marked = highlight("<b>Digitalisierungsstrategie</b>", "fehlt")
    assert "<b>" not in marked
    assert "&lt;b&gt;" in marked
    assert SOFT_HYPHEN in marked


def test_highlight_hyphenates_every_part():
    marked = highlight("Digitalisierungsstrategie Automatisierung", "Automatisierung")
    before, after = marked.split("<em>")
    assert SOFT_HYPHEN in before
    assert SOFT_HYPHEN in after


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30", 30),
        ("30%", 30),
        (" 12.5 ", 12.5),
        ("-5", 0),
        ("140", 100),
        ("", 50),
        ("oben", 50),
        ("nan", 50),
    ],
)
def test_percent_clamps_and_falls_back(value, expected):
    assert percent(value) == expected


def showcase_html(showcase_root, fmt: Format = Format.STORY, **fields) -> str:
    registry = TemplateRegistry.load(showcase_root)
    spec = registry.get(SHOWCASE)
    return registry.render_html(spec, fmt, spec.defaults | spec.samples | fields)


def test_a_template_may_offer_story_first(showcase_root):
    spec = TemplateRegistry.load(showcase_root).get(SHOWCASE)
    assert spec.formats == (Format.STORY, Format.PORTRAIT_4_5)
    assert "--w: 1080px; --h: 1920px;" in showcase_html(showcase_root)


def test_a_template_marks_a_word_with_highlight(showcase_root):
    html = showcase_html(showcase_root, headline="Gute\nKarten", highlight="karten")
    assert "Gute\n<em>Karten</em></main>" in html


def test_a_template_places_a_crop_with_percent(showcase_root):
    assert "--focus-x: 20.0%;" in showcase_html(showcase_root, focus_x="20%")
    assert "--focus-x: 50%;" in showcase_html(showcase_root, focus_x="links")
