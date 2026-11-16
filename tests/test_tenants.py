import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import ROOT_DIR, Settings
from app.tenants import SHORT_FORM_TENANT, Internal
from tests.conftest import (
    ACME,
    ACME_CARD,
    API_KEYS,
    BUILTIN_DIR,
    BUNDLE,
    CARD,
    INTERNAL_KEY,
    MINIMAL_TEMPLATE,
    SOLO,
    SOLO_CARD,
    STUDIO,
    STUDIO_CARD,
    TENANT_KEYS,
    key_hash,
    tenants_for,
    write_bundle,
    write_tenants,
    write_tenants_file,
)

SCRIPT = ROOT_DIR / "scripts" / "new_api_key.py"


def refusal(**arguments) -> str:
    with pytest.raises(RuntimeError) as excinfo:
        tenants_for(**arguments)
    return str(excinfo.value)


def tenants_file(tmp_path: Path, tenants: dict) -> Path:
    return write_tenants_file(tmp_path / "tenants.toml", tenants)


@pytest.fixture
def tenants(tmp_path):
    bundles, path = write_tenants(tmp_path)
    return tenants_for(bundles_dir=bundles, tenants_file=path, internal_key=INTERNAL_KEY)


def test_api_keys_make_up_the_one_tenant_default():
    directory = tenants_for()
    assert directory.names == [SHORT_FORM_TENANT]
    tenant = directory.get(SHORT_FORM_TENANT)
    assert tenant.registry.ids == directory.builtin_ids
    assert tenant.registry.default_template == CARD
    for key in API_KEYS:
        assert directory.authenticate(key) is tenant


def test_keys_are_kept_as_hashes_only():
    tenant = tenants_for().get(SHORT_FORM_TENANT)
    assert tenant.key_hashes == tuple(hashlib.sha256(key.encode()).digest() for key in API_KEYS)


def test_an_unknown_key_authenticates_as_nobody():
    assert tenants_for().authenticate("nope") is None


def test_without_keys_there_is_still_the_one_tenant():
    directory = tenants_for(api_keys=())
    assert directory.has_keys is False
    assert directory.names == [SHORT_FORM_TENANT]


def test_the_one_tenant_finds_its_bundles_in_bundles_dir(tmp_path):
    write_bundle(tmp_path / SHORT_FORM_TENANT, "own")
    directory = tenants_for(bundles_dir=tmp_path, default_template="own/plain")
    registry = directory.get(SHORT_FORM_TENANT).registry
    assert registry.ids == sorted([*directory.builtin_ids, "own/plain"])
    assert registry.default_template == "own/plain"


def test_an_unknown_default_template_stops_the_start():
    assert refusal(default_template="card").startswith(
        f"DEFAULT_TEMPLATE 'card' is not a known template. Available: {CARD}, "
    )


def test_api_keys_next_to_a_tenants_file_stop_the_start(tmp_path):
    _, path = write_tenants(tmp_path)
    assert refusal(tenants_file=path, api_keys=API_KEYS) == (
        "API_KEYS and TENANTS_FILE are both set. Give the keys to the tenants in the tenants "
        "file, or leave TENANTS_FILE empty."
    )


def test_every_tenant_sees_its_own_and_the_built_in_bundles(tenants):
    builtin = tenants.builtin_ids
    assert tenants.names == [ACME, SOLO, STUDIO]
    assert tenants.get(ACME).registry.ids == sorted([*builtin, ACME_CARD])
    assert tenants.get(STUDIO).registry.ids == sorted([*builtin, STUDIO_CARD])
    assert tenants.get(ACME).registry.default_template == CARD


def test_a_tenant_with_a_bundle_list_sees_only_the_listed_bundles(tenants):
    registry = tenants.get(SOLO).registry
    assert registry.ids == [SOLO_CARD]
    assert registry.default_template == SOLO_CARD


def test_the_tenants_share_the_built_in_bundles(tenants):
    acme, studio = tenants.get(ACME).registry, tenants.get(STUDIO).registry
    assert acme.bundle(BUNDLE) is studio.bundle(BUNDLE)


def test_every_key_authenticates_as_its_tenant(tenants):
    for name, key in TENANT_KEYS.items():
        assert tenants.authenticate(key) is tenants.get(name)
    assert tenants.authenticate(INTERNAL_KEY) is Internal.ACCESS
    assert tenants.authenticate(API_KEYS[0]) is None
    assert tenants.key_count == len(TENANT_KEYS)


def test_a_hash_in_upper_case_counts(tmp_path):
    digest = f"sha256:{hashlib.sha256(b'shout').hexdigest().upper()}"
    directory = tenants_for(tenants_file=tenants_file(tmp_path, {ACME: {"api_keys": [digest]}}))
    assert directory.authenticate("shout") is directory.get(ACME)


def test_email_addresses_are_kept_in_lower_case(tmp_path):
    path = tenants_file(tmp_path, {ACME: {"emails": ["Team@Acme.Example"]}})
    assert tenants_for(tenants_file=path).get(ACME).emails == ("team@acme.example",)


def test_a_tenant_without_keys_has_no_way_in_but_the_internal_key(tmp_path):
    path = tenants_file(tmp_path, {ACME: {}})
    assert tenants_for(tenants_file=path).has_keys is False
    assert tenants_for(tenants_file=path, internal_key=INTERNAL_KEY).has_keys is True


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[tenants.acme]\napi_keys = [\n", "The tenants file is not valid TOML"),
        ("version = 1\n[tenants.acme]\n", "The tenants file has the unknown key 'version'."),
        ("", "The tenants file declares no tenant. Add one as [tenants.<name>]."),
        ("tenants = 1\n", "The tenants file declares no tenant."),
        ('[tenants."Acme Corp"]\n', "The tenant name 'Acme Corp' must use lowercase letters"),
        ('[tenants]\nacme = "x"\n', "Tenant 'acme' must be a table, written as [tenants.<name>]."),
        ("[tenants.acme]\nkeys = []\n", "Tenant 'acme' has the unknown key 'keys'."),
        ('[tenants.acme]\napi_keys = "x"\n', "Tenant 'acme' must list its api_keys as strings."),
        ("[tenants.acme]\nemails = [1]\n", "Tenant 'acme' must list its emails as strings."),
        ('[tenants.acme]\nemails = ["acme"]\n', "has the email address 'acme', which is not one."),
        ("[tenants.acme]\nbundles = []\n", "Tenant 'acme' lists no bundle in bundles."),
        (
            '[tenants.acme]\nbundles = ["nope"]\n',
            "lists the bundle 'nope', which is neither one of its own bundles nor a built-in one.",
        ),
        (
            "[tenants.acme]\ndefault_template = 1\n",
            "Tenant 'acme' has a default_template that is not a template id.",
        ),
        (
            '[tenants.acme]\ndefault_template = "demo/nope"\n',
            "has the default_template 'demo/nope', which is not one of its templates.",
        ),
    ],
    ids=[
        "broken toml",
        "unknown key",
        "empty",
        "no table",
        "name",
        "tenant no table",
        "tenant unknown key",
        "api_keys no list",
        "emails no strings",
        "email",
        "empty bundle list",
        "unknown bundle",
        "default no string",
        "unknown default",
    ],
)
def test_an_invalid_tenants_file_stops_the_start(tmp_path, content, message):
    path = tmp_path / "tenants.toml"
    path.write_text(content, encoding="utf-8")
    text = refusal(tenants_file=path)
    assert text.startswith(f"{path}: ")
    assert message in text


def test_a_missing_tenants_file_stops_the_start(tmp_path):
    path = tmp_path / "tenants.toml"
    assert refusal(tenants_file=path) == f"{path}: TENANTS_FILE names a file that does not exist."


@pytest.mark.parametrize(
    "entry",
    ["cg_" + "x" * 43, "a" * 64, "sha256:" + "a" * 63, "sha512:" + "a" * 64, "sha256:" + "g" * 64],
    ids=["key", "bare hex", "short", "other algorithm", "no hex"],
)
def test_an_entry_in_api_keys_that_is_no_hash_stops_the_start_unshown(tmp_path, entry):
    text = refusal(tenants_file=tenants_file(tmp_path, {ACME: {"api_keys": [entry]}}))
    assert "Tenant 'acme' has an entry in api_keys that is no hash" in text
    assert "scripts/new_api_key.py" in text
    assert entry not in text


def test_a_key_of_two_tenants_stops_the_start(tmp_path):
    same = key_hash("shared")
    path = tenants_file(tmp_path, {ACME: {"api_keys": [same]}, STUDIO: {"api_keys": [same]}})
    assert refusal(tenants_file=path) == (
        f"{path}: Tenant 'studio' shares an API key with tenant 'acme'."
    )


def test_a_key_listed_twice_stops_the_start(tmp_path):
    same = key_hash("twice")
    path = tenants_file(tmp_path, {ACME: {"api_keys": [same, same]}})
    assert refusal(tenants_file=path) == f"{path}: Tenant 'acme' lists the same API key twice."


def test_an_email_address_of_two_tenants_stops_the_start(tmp_path):
    path = tenants_file(
        tmp_path, {ACME: {"emails": ["team@example.com"]}, STUDIO: {"emails": ["Team@Example.com"]}}
    )
    assert refusal(tenants_file=path) == (
        f"{path}: Tenant 'studio' shares the email address 'Team@Example.com' with tenant 'acme'."
    )


def test_a_tenant_that_cannot_use_default_template_needs_one_of_its_own(tmp_path):
    write_bundle(tmp_path / "bundles" / ACME, "acme-brand", templates={"card": MINIMAL_TEMPLATE})
    path = tenants_file(tmp_path, {ACME: {"bundles": ["acme-brand"]}})
    assert refusal(bundles_dir=tmp_path / "bundles", tenants_file=path) == (
        f"{path}: Tenant 'acme' cannot use DEFAULT_TEMPLATE {CARD!r}. Give it a "
        f"default_template of its own. Available: {ACME_CARD}."
    )


def test_a_short_internal_key_stops_the_start():
    assert refusal(internal_key="short") == "INTERNAL_KEY must be at least 32 characters long."


def test_an_internal_key_that_is_an_api_key_too_stops_the_start():
    key = "k" * 40
    assert refusal(api_keys=(key,), internal_key=key) == (
        "INTERNAL_KEY is also an API key of tenant 'default'. Give the internal key a value of "
        "its own."
    )


def test_the_internal_key_alone_is_a_key():
    directory = tenants_for(api_keys=(), internal_key=INTERNAL_KEY)
    assert directory.has_keys is True
    assert directory.internal_key is True
    assert directory.authenticate(INTERNAL_KEY) is Internal.ACCESS


def test_a_missing_bundles_dir_stops_the_start(tmp_path):
    missing = tmp_path / "nowhere"
    assert refusal(bundles_dir=missing) == (
        f"{missing}: BUNDLES_DIR names a folder that does not exist."
    )


def test_bundles_dir_on_the_built_in_bundles_stops_the_start():
    assert refusal(bundles_dir=BUILTIN_DIR) == (
        f"{BUILTIN_DIR}: BUNDLES_DIR names the folder of the built-in bundles, which load on "
        "their own. Leave it empty, or name a folder with one subfolder per tenant."
    )


def test_a_bundle_right_in_bundles_dir_stops_the_start(tmp_path):
    bundle = write_bundle(tmp_path)
    text = refusal(bundles_dir=tmp_path)
    assert text.startswith(f"{bundle}: This folder is a bundle, but BUNDLES_DIR holds one folder ")
    assert text.endswith(f"Move it into {tmp_path / SHORT_FORM_TENANT}/.")


def test_a_folder_without_a_tenant_stops_the_start(tmp_path):
    write_bundle(tmp_path / "brand", "brand")
    assert refusal(bundles_dir=tmp_path) == (
        f"{tmp_path / 'brand'}: This folder belongs to no tenant. With API_KEYS the only tenant "
        "is 'default'; name further tenants in a tenants file."
    )


def test_a_folder_without_a_tenant_of_the_file_stops_the_start(tmp_path):
    bundles, path = write_tenants(tmp_path)
    (bundles / "other").mkdir()
    assert refusal(bundles_dir=bundles, tenants_file=path) == (
        f"{bundles / 'other'}: This folder belongs to no tenant. Add the tenant to {path}, or "
        "move the folder out of BUNDLES_DIR."
    )


def test_hidden_and_shared_folders_in_bundles_dir_are_skipped(tmp_path):
    for name in ("_parked", ".git"):
        (tmp_path / name).mkdir()
    (tmp_path / "README.md").write_text("Tenants", encoding="utf-8")
    assert tenants_for(bundles_dir=tmp_path).names == [SHORT_FORM_TENANT]


def test_an_empty_tenant_folder_leaves_the_built_in_bundles(tmp_path):
    (tmp_path / SHORT_FORM_TENANT).mkdir()
    directory = tenants_for(bundles_dir=tmp_path)
    assert directory.get(SHORT_FORM_TENANT).registry.ids == directory.builtin_ids


def test_a_tenant_bundle_named_like_a_built_in_one_stops_the_start(tmp_path):
    write_bundle(tmp_path / SHORT_FORM_TENANT, BUNDLE)
    assert refusal(bundles_dir=tmp_path) == (
        f"{tmp_path / SHORT_FORM_TENANT / BUNDLE}: Bundle 'demo' of tenant 'default' has the "
        "name of a built-in bundle. Give it another name."
    )


def test_a_broken_bundle_of_a_tenant_stops_the_start(tmp_path):
    bundle = write_bundle(tmp_path / SHORT_FORM_TENANT, "own")
    (bundle / "bundle.toml").unlink()
    assert refusal(bundles_dir=tmp_path) == f"{bundle}: Bundle 'own' has no bundle.toml."


def test_without_built_in_bundles_a_tenant_has_only_its_own(tmp_path):
    write_bundle(tmp_path / "bundles" / SHORT_FORM_TENANT, "own")
    directory = tenants_for(
        builtin_dir=tmp_path / "nowhere",
        bundles_dir=tmp_path / "bundles",
        default_template="own/plain",
    )
    assert directory.builtin_ids == []
    assert directory.get(SHORT_FORM_TENANT).registry.ids == ["own/plain"]


@pytest.mark.parametrize("value", ["", "  "])
def test_a_blank_path_setting_counts_as_unset(value):
    settings = Settings(bundles_dir=value, tenants_file=value)
    assert (settings.bundles_dir, settings.tenants_file) == (None, None)


def test_a_blank_variable_wins_over_a_path_in_dotenv(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("BUNDLES_DIR=/srv/tenants\nTENANTS_FILE=/srv/tenants.toml\n")
    monkeypatch.setenv("BUNDLES_DIR", "")
    monkeypatch.setenv("TENANTS_FILE", "")
    settings = Settings(_env_file=dotenv)
    assert (settings.bundles_dir, settings.tenants_file) == (None, None)


def test_a_relative_path_setting_counts_from_the_repository():
    settings = Settings(bundles_dir="tenants", tenants_file="tenants.toml")
    assert settings.bundles_dir == ROOT_DIR / "tenants"
    assert settings.tenants_file == ROOT_DIR / "tenants.toml"


def run_script(*arguments: str, stdin: str | None = None) -> str:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return completed.stdout


def test_the_script_creates_a_key_with_its_hash(tmp_path):
    key_line, hash_line = run_script().splitlines()
    key, digest = key_line.split()[-1], hash_line.split()[-1]
    assert key.startswith("cg_")
    assert len(key) == 46
    assert digest == key_hash(key)
    directory = tenants_for(tenants_file=tenants_file(tmp_path, {ACME: {"api_keys": [digest]}}))
    assert directory.authenticate(key) is directory.get(ACME)


def test_the_script_creates_a_new_key_every_time():
    assert run_script().splitlines()[0] != run_script().splitlines()[0]


def test_the_script_hashes_a_key_from_standard_input():
    assert run_script("--hash", stdin=f"{API_KEYS[0]}\n").strip() == key_hash(API_KEYS[0])
