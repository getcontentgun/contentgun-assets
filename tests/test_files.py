import base64
import re

import pytest

from app import files as files_module
from app.config import settings
from app.errors import ApiError
from app.files import EXPIRES, KIND, Files
from tests.conftest import FILES_SECRET, FILES_URL, SQUARE_SVG, image_base64
from tests.memory_storage import MemoryStorage

NOW = 1_800_000_000
DAY = 86_400
PNG = base64.b64decode(image_base64())
SVG = SQUARE_SVG.encode("utf-8")
CARD = "demo-card_4x5_abcdef12.png"


@pytest.fixture
def clock(monkeypatch):
    moment = {"now": NOW}
    monkeypatch.setattr(files_module, "now", lambda: moment["now"])
    return moment


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
def files(storage, clock) -> Files:
    return Files(storage, FILES_URL, 24, FILES_SECRET)


async def test_an_output_lands_below_its_tenant_with_its_expiry(files, storage):
    saved = await files.save_output("acme", CARD, PNG, "image/png")
    match = re.fullmatch(rf"{FILES_URL}/o/acme/([A-Za-z0-9_-]{{32}})/{CARD}", saved.url)
    assert match is not None
    data, stored = storage.objects[f"outputs/acme/{match.group(1)}/{CARD}"]
    assert data == PNG
    assert stored.content_type == "image/png"
    assert stored.metadata == {EXPIRES: str(NOW + DAY)}
    assert (saved.expires_at, saved.size) == (NOW + DAY, len(PNG))


async def test_every_output_gets_a_url_of_its_own(files):
    first = await files.save_output("acme", CARD, PNG, "image/png")
    second = await files.save_output("acme", CARD, PNG, "image/png")
    assert first.url != second.url


async def test_an_output_is_found_until_it_expires(files, clock):
    saved = await files.save_output("acme", CARD, PNG, "image/png")
    tenant, token, name = saved.url.removeprefix(f"{FILES_URL}/o/").split("/")
    assert (await files.find_output(tenant, token, name)).size == len(PNG)
    clock["now"] = NOW + DAY - 1
    assert files.seconds_left(await files.find_output(tenant, token, name)) == 1
    clock["now"] = NOW + DAY
    assert await files.find_output(tenant, token, name) is None


async def test_an_output_is_found_under_its_own_tenant_only(files):
    saved = await files.save_output("acme", CARD, PNG, "image/png")
    _, token, name = saved.url.removeprefix(f"{FILES_URL}/o/").split("/")
    assert await files.find_output("studio", token, name) is None


@pytest.mark.parametrize(
    ("tenant", "token", "name"),
    [
        ("Acme", "a" * 32, CARD),
        ("acme", "a" * 31, CARD),
        ("acme", "a" * 32, "card.html"),
        ("acme", "a" * 32, "../card.png"),
        ("acme", "a" * 32, "Card.png"),
    ],
)
async def test_a_malformed_output_path_never_reaches_the_storage(
    files, storage, tenant, token, name
):
    storage.failing = True
    assert await files.find_output(tenant, token, name) is None


async def test_an_object_without_an_expiry_is_never_served(files, storage):
    storage.put(f"outputs/acme/{'a' * 32}/{CARD}", PNG, "image/png", {})
    assert await files.find_output("acme", "a" * 32, CARD) is None


def test_the_type_of_an_output_follows_its_name():
    assert Files.output_type(CARD) == "image/png"
    assert Files.output_type("document_4x5_abcdef12.pdf") == "application/pdf"


def token_of(url: str) -> str:
    return url.removeprefix(f"{FILES_URL}/u/")


def test_an_upload_url_names_its_tenant_and_upload(files):
    ticket = files.new_upload("acme")
    assert re.fullmatch(r"up_[A-Za-z0-9_-]{24}", ticket.upload_id)
    assert ticket.url.startswith(f"{FILES_URL}/u/acme.{ticket.upload_id}.")
    assert ticket.expires_at == NOW + 15 * 60
    assert files.check_upload_token(token_of(ticket.url)) == ("acme", ticket.upload_id)


def test_every_upload_url_is_new(files):
    assert files.new_upload("acme").upload_id != files.new_upload("acme").upload_id


def tampered(token: str) -> list[str]:
    tenant, upload_id, expires, signature = token.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    return [
        f"studio.{upload_id}.{expires}.{signature}",
        f"{tenant}.up_{'a' * 24}.{expires}.{signature}",
        f"{tenant}.{upload_id}.{int(expires) + 3600}.{signature}",
        f"{tenant}.{upload_id}.{expires}.{flipped}",
        f"{tenant}.{upload_id}.{expires}",
        f"{tenant}.{upload_id}.{expires}.{signature}.x",
        "",
    ]


def test_a_tampered_upload_url_is_refused(files):
    for token in tampered(token_of(files.new_upload("acme").url)):
        with pytest.raises(ApiError) as excinfo:
            files.check_upload_token(token)
        assert (excinfo.value.status_code, excinfo.value.message) == (
            403,
            "The upload URL is not valid.",
        ), token


def test_an_upload_url_of_another_secret_is_refused(files, storage):
    token = token_of(Files(storage, FILES_URL, 24, "another secret").new_upload("acme").url)
    with pytest.raises(ApiError) as excinfo:
        files.check_upload_token(token)
    assert excinfo.value.status_code == 403


def test_an_upload_url_expires_after_fifteen_minutes(files, clock):
    token = token_of(files.new_upload("acme").url)
    clock["now"] = NOW + 15 * 60 - 1
    files.check_upload_token(token)
    clock["now"] = NOW + 15 * 60
    with pytest.raises(ApiError) as excinfo:
        files.check_upload_token(token)
    assert excinfo.value.status_code == 403
    assert excinfo.value.message.startswith("The upload URL has expired.")


@pytest.mark.parametrize(
    ("data", "kind", "content_type"),
    [(PNG, "image", "image/png"), (SVG, "icon", "image/svg+xml")],
    ids=["image", "icon"],
)
async def test_an_upload_is_kept_with_its_kind(files, storage, data, kind, content_type):
    upload_id = files.new_upload("acme").upload_id
    saved = await files.save_upload("acme", upload_id, data)
    assert (saved.kind, saved.content_type, saved.size) == (kind, content_type, len(data))
    stored_data, stored = storage.objects[f"uploads/acme/{upload_id}"]
    assert stored_data == data
    assert stored.content_type == content_type
    assert stored.metadata == {EXPIRES: str(NOW + DAY), KIND: kind}


async def test_an_upload_url_takes_one_upload(files):
    upload_id = files.new_upload("acme").upload_id
    await files.save_upload("acme", upload_id, PNG)
    with pytest.raises(ApiError) as excinfo:
        await files.save_upload("acme", upload_id, PNG)
    assert excinfo.value.status_code == 409


@pytest.mark.parametrize(
    ("data", "status", "message"),
    [
        (b"", 400, "The upload is empty."),
        (b"GIF89a", 400, "The upload must be a PNG, JPEG or WebP image or an SVG icon."),
        (b"<html></html>", 400, "The uploaded SVG must be an SVG document."),
        (
            SVG.replace(b"<rect", b"<animate/><rect"),
            400,
            "The uploaded SVG must not be animated.",
        ),
    ],
    ids=["empty", "gif", "html", "animated"],
)
async def test_an_upload_gets_the_checks_of_a_request(files, data, status, message):
    with pytest.raises(ApiError) as excinfo:
        await files.save_upload("acme", files.new_upload("acme").upload_id, data)
    assert (excinfo.value.status_code, excinfo.value.message) == (status, message)


async def test_an_uploaded_svg_keeps_the_icon_limit(files, monkeypatch):
    monkeypatch.setattr(settings, "max_image_bytes", 65_536)
    padded = SVG.replace(b"<rect", b"<!--" + b"x" * 70_000 + b"--><rect")
    with pytest.raises(ApiError) as excinfo:
        await files.save_upload("acme", files.new_upload("acme").upload_id, padded)
    assert (excinfo.value.status_code, excinfo.value.message) == (
        413,
        "An SVG icon must be at most 64 KB.",
    )


async def uploaded(files: Files, data: bytes, tenant: str = "acme") -> str:
    upload_id = files.new_upload(tenant).upload_id
    await files.save_upload(tenant, upload_id, data)
    return upload_id


async def test_a_tenant_reads_its_upload(files):
    upload_id = await uploaded(files, PNG)
    assert await files.uploads_of("acme").read(upload_id, "image") == PNG


async def test_an_upload_of_another_tenant_is_unknown(files):
    upload_id = await uploaded(files, PNG)
    with pytest.raises(ApiError) as excinfo:
        await files.uploads_of("studio").read(upload_id, "image")
    assert excinfo.value.message == (
        f"Unknown upload {upload_id!r}. An upload is kept for 24 hours after it arrives."
    )


async def test_an_expired_upload_is_unknown(files, clock):
    upload_id = await uploaded(files, PNG)
    clock["now"] = NOW + DAY
    with pytest.raises(ApiError) as excinfo:
        await files.uploads_of("acme").find(upload_id, "image")
    assert excinfo.value.message.startswith(f"Unknown upload {upload_id!r}.")


@pytest.mark.parametrize(
    ("data", "wanted", "message"),
    [
        (SVG, "image", "is an icon, so send it as icon_upload."),
        (PNG, "icon", "is an image, so send it as image_upload."),
    ],
)
async def test_an_upload_is_used_as_what_it_is(files, data, wanted, message):
    upload_id = await uploaded(files, data)
    with pytest.raises(ApiError) as excinfo:
        await files.uploads_of("acme").find(upload_id, wanted)
    assert excinfo.value.message == f"Upload {upload_id!r} {message}"


async def test_a_malformed_upload_id_is_named_as_such(files, storage):
    storage.failing = True
    with pytest.raises(ApiError) as excinfo:
        await files.uploads_of("acme").find("photo.png", "image")
    assert (
        excinfo.value.message == "Field 'image_upload' must be an upload id from POST /v1/uploads."
    )


async def test_an_unreachable_storage_is_a_503(files, storage):
    upload_id = files.new_upload("acme").upload_id
    storage.failing = True
    for call in (
        files.save_output("acme", CARD, PNG, "image/png"),
        files.find_output("acme", "a" * 32, CARD),
        files.save_upload("acme", upload_id, PNG),
        files.uploads_of("acme").find(upload_id, "image"),
    ):
        with pytest.raises(ApiError) as excinfo:
            await call
        assert (excinfo.value.status_code, excinfo.value.message) == (
            503,
            "The file storage is not available, retry later.",
        )
