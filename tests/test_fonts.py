import shutil

import pytest

from app.fonts import LATIN, LATIN_EXT, MIN_FONT_BYTES, WOFF2_SIGNATURE, load_font_set
from app.templating import TemplateRegistry
from tests.conftest import BUNDLE, BUNDLES_DIR, source_face


@pytest.fixture
def fonts_dir(tmp_path):
    directory = tmp_path / "mini" / "fonts"
    directory.mkdir(parents=True)
    source = BUNDLES_DIR / BUNDLE / "fonts" / source_face()["file"]
    shutil.copyfile(source, directory / "face.woff2")
    return directory


def face(**overrides) -> dict:
    return {"family": "Mini Sans", "file": "face.woff2", "weight": "400"} | overrides


def load(fonts_dir, faces=None, checks=None, table=None):
    if table is None:
        table = {
            "faces": [face()] if faces is None else faces,
            "checks": ['400 20px "Mini Sans"'] if checks is None else checks,
        }
    return load_font_set(table, fonts_dir, fonts_dir.parent / "bundle.toml", "mini")


def refusal(fonts_dir, **kwargs) -> str:
    with pytest.raises(RuntimeError) as excinfo:
        load(fonts_dir, **kwargs)
    return str(excinfo.value)


def test_the_suite_bundle_embeds_every_face_once():
    fonts = TemplateRegistry.load(BUNDLES_DIR).bundle(BUNDLE).fonts
    assert fonts.css.count("@font-face") == len(fonts.faces)
    assert fonts.css.count('format("woff2")') == len(fonts.faces)
    for declared in fonts.faces:
        data = (BUNDLES_DIR / BUNDLE / "fonts" / declared.file).read_bytes()
        assert len(data) >= MIN_FONT_BYTES
        assert data.startswith(WOFF2_SIGNATURE)


@pytest.mark.parametrize(("subset", "unicode_range"), [("latin", LATIN), ("latin-ext", LATIN_EXT)])
def test_a_subset_becomes_its_unicode_range(fonts_dir, subset, unicode_range):
    fonts = load(fonts_dir, faces=[face(subset=subset)])
    assert fonts.faces[0].unicode_range == unicode_range
    assert f"unicode-range: {unicode_range};" in fonts.css


def test_an_explicit_unicode_range_is_kept(fonts_dir):
    fonts = load(fonts_dir, faces=[face(unicode_range="U+0000-00FF, U+20AC")])
    assert "unicode-range: U+0000-00FF, U+20AC;" in fonts.css


def test_a_face_without_a_range_covers_every_character(fonts_dir):
    fonts = load(fonts_dir)
    assert fonts.faces[0].unicode_range is None
    assert "unicode-range" not in fonts.css


def test_the_face_is_embedded_as_a_data_uri(fonts_dir):
    css = load(fonts_dir).css
    assert 'font-family: "Mini Sans";' in css
    assert 'src: url("data:font/woff2;base64,' in css
    assert "font-display: block;" in css


@pytest.mark.parametrize(("weight", "declared"), [(400, "400"), ("200 900", "200 900")])
def test_a_weight_is_a_number_or_a_range(fonts_dir, weight, declared):
    fonts = load(fonts_dir, faces=[face(weight=weight)])
    assert f"font-weight: {declared};" in fonts.css


def test_a_range_covers_every_weight_inside_it(fonts_dir):
    checks = ['200 20px "Mini Sans"', '800 20px "Mini Sans"']
    assert load(fonts_dir, faces=[face(weight="200 900")], checks=checks).checks == tuple(checks)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"family": 'Mini "Sans"'}, "has a font face with the family 'Mini \"Sans\"'"),
        ({"family": " Mini"}, "has a font face with the family ' Mini'"),
        ({"family": None}, "has a font face with the family None"),
        ({"file": "../face.woff2"}, "names the font file '../face.woff2'"),
        ({"file": None}, "names the font file None"),
        ({"weight": "bold"}, "gives 'face.woff2' the weight 'bold'"),
        ({"weight": "900 200"}, "gives 'face.woff2' the weight '900 200'"),
        ({"weight": "0"}, "gives 'face.woff2' the weight '0'"),
        ({"weight": True}, "gives 'face.woff2' the weight True"),
        ({"subset": "cyrillic"}, "the subset 'cyrillic'. Known subsets: latin, latin-ext"),
        ({"subset": "latin", "unicode_range": "U+0000"}, "both a subset and a unicode_range"),
        ({"unicode_range": "0000-00FF"}, "the unicode_range '0000-00FF'"),
        ({"style": "italic"}, "has a font face with the unknown key 'style'"),
    ],
    ids=[
        "quote in family",
        "padded family",
        "no family",
        "path as file",
        "no file",
        "named weight",
        "reversed range",
        "weight zero",
        "boolean weight",
        "unknown subset",
        "subset and range",
        "malformed range",
        "unknown key",
    ],
)
def test_an_invalid_face_is_refused(fonts_dir, overrides, message):
    text = refusal(fonts_dir, faces=[face(**overrides)])
    assert message in text
    assert text.startswith(f"{fonts_dir.parent / 'bundle.toml'}: Bundle 'mini' ")


@pytest.mark.parametrize(
    ("check", "message"),
    [
        ('700 20px "Mini Sans"', "but no font face of 'Mini Sans' covers the weight 700"),
        ('400 20px "Other Sans"', "but no font face of 'Other Sans' covers the weight 400"),
        ('bold 20px "Mini Sans"', "is not of the form"),
        ("400 20px Mini Sans", "is not of the form"),
    ],
    ids=["uncovered weight", "undeclared family", "named weight", "unquoted family"],
)
def test_a_check_must_name_a_weight_a_face_covers(fonts_dir, check, message):
    assert message in refusal(fonts_dir, checks=[check])


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ({"faces": [], "checks": ['400 20px "Mini Sans"']}, "declares no font faces"),
        ({"checks": ['400 20px "Mini Sans"']}, "declares no font faces"),
        ({"faces": [face()], "checks": []}, "declares no font checks"),
        ({"faces": [face()]}, "declares no font checks"),
        ({"faces": [face()], "checks": [400]}, "declares no font checks"),
        ("Mini Sans", "must declare [fonts] as a table"),
        ({"faces": [face()], "checks": [], "family": "x"}, "unknown key 'family' in [fonts]"),
    ],
    ids=[
        "empty faces",
        "no faces",
        "empty checks",
        "no checks",
        "check as a number",
        "not a table",
        "unknown key",
    ],
)
def test_a_bundle_needs_faces_and_checks(fonts_dir, table, message):
    assert message in refusal(fonts_dir, table=table)


def test_a_bundle_without_a_fonts_table_needs_one(fonts_dir):
    with pytest.raises(RuntimeError, match="declares no font faces"):
        load_font_set(None, fonts_dir, fonts_dir.parent / "bundle.toml", "mini")


def test_every_unusable_file_is_named_in_one_message(fonts_dir):
    (fonts_dir / "short.woff2").write_bytes(WOFF2_SIGNATURE)
    (fonts_dir / "plain.woff2").write_bytes(b"x" * MIN_FONT_BYTES)
    faces = [
        face(),
        face(file="gone.woff2"),
        face(file="short.woff2"),
        face(file="plain.woff2"),
    ]
    text = refusal(fonts_dir, faces=faces)
    assert text.startswith(f"{fonts_dir}: Font files of bundle 'mini' are unusable: ")
    assert "gone.woff2 is missing" in text
    assert f"short.woff2 is only {len(WOFF2_SIGNATURE)} bytes" in text
    assert "plain.woff2 is not a woff2 file" in text
    assert "face.woff2" not in text


def test_a_font_file_linked_from_outside_is_refused(fonts_dir):
    outside = fonts_dir.parent.parent / "outside.woff2"
    shutil.copyfile(fonts_dir / "face.woff2", outside)
    (fonts_dir / "linked.woff2").symlink_to(outside)
    assert "linked.woff2 lies outside fonts/" in refusal(
        fonts_dir, faces=[face(file="linked.woff2")]
    )
