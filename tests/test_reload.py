import pytest

from app.config import settings
from app.main import create_app
from app.reloading import BundleCache, download_bundles
from tests.conftest import (
    ACME,
    AUTH,
    INTERNAL_KEY,
    MINIMAL_TEMPLATE,
    STUDIO,
    bearer,
    client_of,
    files_of,
    key_hash,
    tenant_auth,
    tenants_for,
    write_bundle,
    write_tenants,
    write_tenants_file,
)
from tests.memory_storage import MemoryStorage

RELOAD = "/v1/admin/reload"
INTERNAL = bearer(INTERNAL_KEY)


async def template_ids(client, headers) -> list[str]:
    response = await client.get("/v1/templates", headers=headers)
    assert response.status_code == 200, response.text
    return [template["id"] for template in response.json()["templates"]]


@pytest.fixture
def folders(tmp_path):
    return write_tenants(tmp_path)


@pytest.fixture
async def folder_client(folders):
    bundles, tenants_file = folders
    app = create_app(
        tenants_for(bundles_dir=bundles, tenants_file=tenants_file, internal_key=INTERNAL_KEY)
    )
    async with client_of(app) as client:
        yield client


async def test_reload_needs_the_internal_key(folder_client):
    for headers in ({}, tenant_auth(ACME), bearer("nope")):
        response = await folder_client.post(RELOAD, headers=headers)
        assert (response.status_code, response.json()["error"]) == (401, "Unauthorized.")


async def test_reload_lists_the_tenants(folder_client):
    response = await folder_client.post(RELOAD, headers=INTERNAL)
    assert response.status_code == 200
    names = [tenant["name"] for tenant in response.json()["tenants"]]
    assert names == sorted([ACME, STUDIO, "solo"])


async def test_reload_reads_the_bundle_folders_again(folder_client, folders):
    bundles, _ = folders
    write_bundle(bundles / ACME, "acme-extra", templates={"poster": MINIMAL_TEMPLATE})
    assert "acme-extra/poster" not in await template_ids(folder_client, tenant_auth(ACME))
    assert (await folder_client.post(RELOAD, headers=INTERNAL)).status_code == 200
    assert "acme-extra/poster" in await template_ids(folder_client, tenant_auth(ACME))
    assert "acme-extra/poster" not in await template_ids(folder_client, tenant_auth(STUDIO))


async def test_reload_reads_the_tenants_file_again(folder_client, folders):
    _, tenants_file = folders
    tenants_file.write_text(
        tenants_file.read_text(encoding="utf-8")
        + f'\n[tenants.nova]\napi_keys = ["{key_hash("nova-key")}"]\n',
        encoding="utf-8",
    )
    assert (await folder_client.get("/v1/templates", headers=bearer("nova-key"))).status_code == 401
    assert (await folder_client.post(RELOAD, headers=INTERNAL)).status_code == 200
    assert (await folder_client.get("/v1/templates", headers=bearer("nova-key"))).status_code == 200


async def test_a_broken_bundle_keeps_what_is_in_use(folder_client, folders):
    bundles, _ = folders
    before = await template_ids(folder_client, tenant_auth(ACME))
    broken = write_bundle(bundles / ACME, "acme-broken")
    (broken / "bundle.toml").write_text('name = "acme-broken"\n[fonts\n', encoding="utf-8")
    response = await folder_client.post(RELOAD, headers=INTERNAL)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error.startswith("Nothing was reloaded, the tenants and bundles in use stay active. ")
    assert f"{broken.resolve()}/bundle.toml: Bundle 'acme-broken' is not valid TOML" in error
    assert await template_ids(folder_client, tenant_auth(ACME)) == before


@pytest.fixture
def bucket(tmp_path) -> MemoryStorage:
    bucket = MemoryStorage()
    bucket.put_folder(
        "templates/default/bucket-cards",
        write_bundle(tmp_path / "source", "bucket-cards", templates={"card": MINIMAL_TEMPLATE}),
    )
    return bucket


@pytest.fixture
def bucket_app(bucket, monkeypatch):
    monkeypatch.setattr(settings, "internal_key", INTERNAL_KEY)
    app = create_app(files=files_of(bucket))
    yield app
    app.state.reloader.close()


@pytest.fixture
async def bucket_client(bucket_app):
    async with client_of(bucket_app) as client:
        yield client


async def test_the_bundles_of_a_tenant_come_from_the_bucket(bucket_client):
    assert "bucket-cards/card" in await template_ids(bucket_client, AUTH)


async def test_reload_copies_the_bucket_again(bucket_client, bucket, tmp_path):
    source = write_bundle(
        tmp_path / "more", "bucket-posters", templates={"poster": MINIMAL_TEMPLATE}
    )
    bucket.put_folder("templates/default/bucket-posters", source)
    assert "bucket-posters/poster" not in await template_ids(bucket_client, AUTH)
    assert (await bucket_client.post(RELOAD, headers=INTERNAL)).status_code == 200
    ids = await template_ids(bucket_client, AUTH)
    assert {"bucket-cards/card", "bucket-posters/poster"} <= set(ids)


async def test_a_broken_bundle_in_the_bucket_is_named_by_its_key(bucket_client, bucket):
    bucket.put("templates/default/bucket-cards/bundle.toml", b"[fonts\n", "text/plain", {})
    response = await bucket_client.post(RELOAD, headers=INTERNAL)
    assert response.status_code == 409
    assert (
        "memory://test/templates/default/bucket-cards/bundle.toml: Bundle 'bucket-cards' is not "
        "valid TOML"
    ) in response.json()["error"]
    assert "bucket-cards/card" in await template_ids(bucket_client, AUTH)


async def test_an_unreachable_bucket_keeps_what_is_in_use(bucket_client, bucket):
    bucket.failing = True
    response = await bucket_client.post(RELOAD, headers=INTERNAL)
    assert (response.status_code, response.json()["error"]) == (
        503,
        "The file storage is not available, retry later.",
    )
    bucket.failing = False
    assert "bucket-cards/card" in await template_ids(bucket_client, AUTH)


def test_a_broken_bundle_in_the_bucket_stops_the_start(bucket):
    bucket.put("templates/default/bucket-cards/bundle.toml", b"[fonts\n", "text/plain", {})
    with pytest.raises(RuntimeError) as excinfo:
        create_app(files=files_of(bucket))
    assert str(excinfo.value).startswith(
        "memory://test/templates/default/bucket-cards/bundle.toml: Bundle 'bucket-cards'"
    )


def test_an_unreachable_bucket_stops_the_start(bucket):
    bucket.failing = True
    with pytest.raises(RuntimeError) as excinfo:
        create_app(files=files_of(bucket))
    assert str(excinfo.value).startswith("The tenant bundles could not be copied: ")


def test_a_folder_of_a_tenant_wins_over_the_bucket(bucket, tmp_path, monkeypatch):
    bundles = tmp_path / "local"
    write_bundle(bundles / "default", "local-cards", templates={"card": MINIMAL_TEMPLATE})
    monkeypatch.setattr(settings, "bundles_dir", bundles)
    app = create_app(files=files_of(bucket))
    ids = app.state.tenants.get("default").registry.ids
    assert "local-cards/card" in ids
    assert "bucket-cards/card" not in ids


@pytest.mark.parametrize(
    "key",
    [
        "templates/default/../escape.toml",
        "templates/default/./card/template.toml",
        "templates/default//card/template.toml",
        "templates/default/card\\template.toml",
    ],
)
def test_a_key_that_is_no_plain_path_is_not_copied(tmp_path, key):
    storage = MemoryStorage()
    storage.put(key, b"x", "text/plain", {})
    with pytest.raises(RuntimeError) as excinfo:
        download_bundles(storage, tmp_path.resolve())
    assert str(excinfo.value).startswith(f"memory://test/{key}: This key is no plain path")
    assert list(tmp_path.iterdir()) == []


def test_folder_markers_are_skipped(tmp_path):
    storage = MemoryStorage()
    storage.put("templates/default/", b"", "application/x-directory", {})
    storage.put("templates/default/notes.txt", b"hello", "text/plain", {})
    assert download_bundles(storage, tmp_path.resolve()) == 1
    assert (tmp_path / "default" / "notes.txt").read_bytes() == b"hello"


def test_the_cache_keeps_the_copy_in_use_and_the_one_before(tmp_path):
    cache = BundleCache(MemoryStorage(), tmp_path / "cache")
    folders = []
    for _ in range(3):
        folder = cache.fill()
        cache.keep(folder)
        folders.append(folder)
    assert [folder.exists() for folder in folders] == [False, True, True]
    cache.close()
    assert not (tmp_path / "cache").exists()


def test_a_tenants_file_drives_the_bundles_in_the_bucket(bucket, tmp_path, monkeypatch):
    tenants_file = write_tenants_file(
        tmp_path / "tenants.toml",
        {
            "default": {
                "api_keys": [key_hash("bucket-key")],
                "default_template": "bucket-cards/card",
            }
        },
    )
    monkeypatch.setattr(settings, "api_keys", "")
    monkeypatch.setattr(settings, "tenants_file", tenants_file)
    app = create_app(files=files_of(bucket))
    assert app.state.tenants.get("default").registry.default_template == "bucket-cards/card"
    app.state.reloader.close()
