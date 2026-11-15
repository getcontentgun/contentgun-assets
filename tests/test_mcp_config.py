import pytest

from config import DEFAULT_BASE_URL, DEFAULT_TIMEOUT_S, ConfigError, load_config

KEY = "client-key"


@pytest.fixture
def environ(tmp_path):
    return {"ASSETS_API_KEY": KEY, "ASSETS_OUTPUT_ROOT": str(tmp_path)}


def test_defaults_apply_to_a_minimal_environment(environ, tmp_path):
    config = load_config(environ)
    assert config.api_key == KEY
    assert config.base_url == DEFAULT_BASE_URL
    assert config.timeout_s == DEFAULT_TIMEOUT_S
    assert config.output_root == tmp_path.resolve()
    assert config.image_roots == (tmp_path.resolve(),)
    assert config.bundle is None


def test_reads_the_process_environment_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSETS_API_KEY", KEY)
    monkeypatch.setenv("ASSETS_OUTPUT_ROOT", str(tmp_path))
    assert load_config().api_key == KEY


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_key_is_named(environ, value):
    if value is None:
        del environ["ASSETS_API_KEY"]
    else:
        environ["ASSETS_API_KEY"] = value
    with pytest.raises(ConfigError, match="ASSETS_API_KEY"):
        load_config(environ)


def test_trailing_slash_is_dropped(environ):
    environ["ASSETS_BASE_URL"] = "http://127.0.0.1:8010/"
    assert load_config(environ).base_url == "http://127.0.0.1:8010"


@pytest.mark.parametrize("value", ["127.0.0.1:8010", "ftp://example.org", "http://"])
def test_base_url_needs_http_and_a_host(environ, value):
    environ["ASSETS_BASE_URL"] = value
    with pytest.raises(ConfigError, match="ASSETS_BASE_URL"):
        load_config(environ)


def test_timeout_accepts_fractions(environ):
    environ["ASSETS_TIMEOUT_S"] = "2.5"
    assert load_config(environ).timeout_s == 2.5


@pytest.mark.parametrize("value", ["0", "-1", "soon", "nan", "inf"])
def test_timeout_must_be_a_positive_number(environ, value):
    environ["ASSETS_TIMEOUT_S"] = value
    with pytest.raises(ConfigError, match="ASSETS_TIMEOUT_S"):
        load_config(environ)


def test_missing_output_root_is_named(environ):
    del environ["ASSETS_OUTPUT_ROOT"]
    with pytest.raises(ConfigError, match="ASSETS_OUTPUT_ROOT"):
        load_config(environ)


@pytest.mark.parametrize("value", ["relative/folder", "/does/not/exist"])
def test_output_root_must_be_an_existing_absolute_directory(environ, value):
    environ["ASSETS_OUTPUT_ROOT"] = value
    with pytest.raises(ConfigError, match="ASSETS_OUTPUT_ROOT"):
        load_config(environ)


def test_output_root_cannot_be_a_file(environ, tmp_path):
    file = tmp_path / "file.txt"
    file.write_text("x")
    environ["ASSETS_OUTPUT_ROOT"] = str(file)
    with pytest.raises(ConfigError, match="ASSETS_OUTPUT_ROOT"):
        load_config(environ)


def test_image_roots_are_split_on_colons(environ, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    environ["ASSETS_IMAGE_ROOTS"] = f"{first}:{second}:"
    assert load_config(environ).image_roots == (first.resolve(), second.resolve())


def test_image_roots_must_exist(environ):
    environ["ASSETS_IMAGE_ROOTS"] = "/does/not/exist"
    with pytest.raises(ConfigError, match="ASSETS_IMAGE_ROOTS"):
        load_config(environ)


def test_the_bundle_is_read_and_stripped(environ):
    environ["ASSETS_BUNDLE"] = " brand "
    assert load_config(environ).bundle == "brand"


def test_an_empty_bundle_counts_as_none(environ):
    environ["ASSETS_BUNDLE"] = "  "
    assert load_config(environ).bundle is None


@pytest.mark.parametrize("value", ["brand/card", "Brand", "-brand"])
def test_the_bundle_must_be_a_bundle_name(environ, value):
    environ["ASSETS_BUNDLE"] = value
    with pytest.raises(ConfigError, match="ASSETS_BUNDLE"):
        load_config(environ)
