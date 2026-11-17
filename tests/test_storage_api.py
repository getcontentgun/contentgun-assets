import base64
import re
from urllib.parse import urlsplit

import pytest

from app import files as files_module
from app.config import settings
from tests.conftest import (
    ACME,
    AUTH,
    CARD,
    CARD_IMAGE,
    CONTENT,
    FILES_URL,
    SQUARE_SVG,
    STUDIO,
    image_base64,
    tenant_auth,
)

PNG = base64.b64decode(image_base64())
SVG = SQUARE_SVG.encode("utf-8")
UNAVAILABLE = "The file storage is not available, retry later."
NO_STORAGE = "Storage is not configured on this server, so {} cannot be used."


def path_of(url: str) -> str:
    assert url.startswith(f"{FILES_URL}/")
    return urlsplit(url).path


async def upload(client, data: bytes, tenant: str = ACME) -> str:
    ticket = await client.post("/v1/uploads", headers=tenant_auth(tenant))
    assert ticket.status_code == 200, ticket.text
    response = await client.put(path_of(ticket.json()["upload_url"]), content=data)
    assert response.status_code == 201, response.text
    return response.json()["upload_id"]


async def render(client, tenant: str = ACME, **payload):
    body = {"headline": "Hallo"} | payload
    return await client.post("/v1/render", headers=tenant_auth(tenant), json=body)


async def test_output_url_needs_storage(client):
    for path, body in (
        ("/v1/render", {"headline": "Hallo", "output": "url"}),
        ("/v1/document", {"slides": [{"headline": "Hallo"}], "output": "url"}),
    ):
        response = await client.post(path, headers=AUTH, json=body)
        assert response.status_code == 501
        assert response.json()["error"] == NO_STORAGE.format("output 'url'")


async def test_uploads_need_storage(client):
    response = await client.post("/v1/uploads", headers=AUTH)
    assert (response.status_code, response.json()["error"]) == (501, NO_STORAGE.format("uploads"))
    assert (await client.put("/u/anything", content=PNG)).status_code == 501


async def test_an_upload_in_a_request_needs_storage(client):
    response = await client.post(
        "/v1/render",
        headers=AUTH,
        json={"template": CARD_IMAGE, "headline": "Hallo", "image_upload": "up_" + "a" * 24},
    )
    assert response.status_code == 501
    assert response.json()["error"] == NO_STORAGE.format("image_upload")


async def test_output_is_binary_or_url(client):
    response = await client.post(
        "/v1/render", headers=AUTH, json={"headline": "Hallo", "output": "pdf"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "output: Input should be 'binary' or 'url'."


async def test_without_storage_no_output_exists(client):
    response = await client.get(f"/o/default/{'a' * 32}/demo-card_4x5_abcdef12.png")
    assert response.status_code == 404


async def test_an_upload_url_is_issued_to_a_tenant(storage_client):
    response = await storage_client.post("/v1/uploads", headers=tenant_auth(ACME))
    assert response.status_code == 200
    body = response.json()
    assert re.fullmatch(r"up_[A-Za-z0-9_-]{24}", body["upload_id"])
    assert body["upload_url"].startswith(f"{FILES_URL}/u/{ACME}.{body['upload_id']}.")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", body["expires_at"])
    assert body["max_bytes"] == settings.max_image_bytes


async def test_an_upload_url_needs_a_key(storage_client):
    assert (await storage_client.post("/v1/uploads")).status_code == 401


async def test_an_upload_arrives_with_a_plain_put(storage_client, storage):
    ticket = (await storage_client.post("/v1/uploads", headers=tenant_auth(ACME))).json()
    response = await storage_client.put(path_of(ticket["upload_url"]), content=PNG)
    assert response.status_code == 201
    body = response.json()
    assert body["upload_id"] == ticket["upload_id"]
    assert (body["kind"], body["content_type"], body["bytes"]) == ("image", "image/png", len(PNG))
    assert f"uploads/{ACME}/{ticket['upload_id']}" in storage.objects


async def test_an_upload_url_takes_one_upload(storage_client):
    ticket = (await storage_client.post("/v1/uploads", headers=tenant_auth(ACME))).json()
    path = path_of(ticket["upload_url"])
    assert (await storage_client.put(path, content=PNG)).status_code == 201
    response = await storage_client.put(path, content=PNG)
    assert response.status_code == 409
    assert response.json()["error"].startswith("The upload URL has been used already.")


async def test_a_tampered_upload_url_is_refused(storage_client):
    ticket = (await storage_client.post("/v1/uploads", headers=tenant_auth(ACME))).json()
    path = path_of(ticket["upload_url"]).replace(f"/u/{ACME}.", f"/u/{STUDIO}.")
    response = await storage_client.put(path, content=PNG)
    assert (response.status_code, response.json()["error"]) == (403, "The upload URL is not valid.")


async def test_an_expired_upload_url_is_refused(storage_client, monkeypatch):
    ticket = (await storage_client.post("/v1/uploads", headers=tenant_auth(ACME))).json()
    later = files_module.now() + 15 * 60
    monkeypatch.setattr(files_module, "now", lambda: later)
    response = await storage_client.put(path_of(ticket["upload_url"]), content=PNG)
    assert response.status_code == 403
    assert response.json()["error"].startswith("The upload URL has expired.")


async def test_an_upload_url_of_an_unknown_tenant_is_refused(storage_client, storage_app):
    ticket = storage_app.state.files.new_upload("ghost")
    response = await storage_client.put(path_of(ticket.url), content=PNG)
    assert response.status_code == 403


async def test_an_upload_over_the_limit_is_refused(storage_client, monkeypatch):
    monkeypatch.setattr(settings, "max_image_bytes", 65_536)
    ticket = (await storage_client.post("/v1/uploads", headers=tenant_auth(ACME))).json()
    response = await storage_client.put(path_of(ticket["upload_url"]), content=PNG + b"x" * 70_000)
    assert response.status_code == 413
    assert response.json()["error"] == "An upload must be at most 64 KB."


async def test_an_upload_that_is_no_image_is_refused(storage_client):
    ticket = (await storage_client.post("/v1/uploads", headers=tenant_auth(ACME))).json()
    response = await storage_client.put(path_of(ticket["upload_url"]), content=b"GIF89a")
    assert response.status_code == 400


async def test_a_render_takes_an_uploaded_image(storage_client):
    upload_id = await upload(storage_client, PNG)
    response = await render(storage_client, template=CARD_IMAGE, image_upload=upload_id)
    assert (response.status_code, response.json()["error"]) == (503, "Renderer is not available.")


async def test_an_uploaded_icon_goes_into_icon_upload(storage_client):
    upload_id = await upload(storage_client, SVG)
    response = await render(storage_client, template=CONTENT, image_upload=upload_id)
    assert response.status_code == 400
    assert (
        response.json()["error"] == f"Upload {upload_id!r} is an icon, so send it as icon_upload."
    )
    response = await render(storage_client, template=CONTENT, icon_upload=upload_id)
    assert response.status_code == 503


async def test_an_upload_of_another_tenant_is_unknown(storage_client):
    upload_id = await upload(storage_client, PNG, ACME)
    response = await render(storage_client, STUDIO, template=CARD_IMAGE, image_upload=upload_id)
    assert response.status_code == 400
    assert response.json()["error"].startswith(f"Unknown upload {upload_id!r}.")


async def test_an_image_comes_from_one_source(storage_client):
    upload_id = await upload(storage_client, PNG)
    response = await render(
        storage_client, template=CARD_IMAGE, image=image_base64(), image_upload=upload_id
    )
    assert response.status_code == 400
    assert response.json()["error"] == (
        "Send the image in one field only: image, image_upload or image_url."
    )


async def test_an_uploaded_image_and_an_icon_are_refused_together(storage_client):
    upload_id = await upload(storage_client, PNG)
    icon_id = await upload(storage_client, SVG)
    response = await render(
        storage_client, template=CONTENT, image_upload=upload_id, icon_upload=icon_id
    )
    assert response.status_code == 400
    assert response.json()["error"] == "Send either an image or an icon, not both."


async def test_a_template_without_images_refuses_an_upload(storage_client):
    upload_id = await upload(storage_client, PNG)
    response = await render(storage_client, template=CARD, image_upload=upload_id)
    assert response.status_code == 400
    assert response.json()["error"] == f"Template {CARD!r} does not accept an image."


async def test_an_unknown_upload_is_refused(storage_client):
    response = await render(storage_client, template=CARD_IMAGE, image_upload="up_" + "a" * 24)
    assert response.status_code == 400
    assert response.json()["error"].startswith("Unknown upload 'up_")
    response = await render(storage_client, template=CARD_IMAGE, image_upload="photo.png")
    assert response.json()["error"] == (
        "Field 'image_upload' must be an upload id from POST /v1/uploads."
    )


async def test_output_belongs_to_the_document(storage_client):
    slides = [{"headline": "Eins"}, {"headline": "Zwei", "output": "url"}]
    response = await storage_client.post(
        "/v1/document", headers=tenant_auth(ACME), json={"slides": slides}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "Slide 2: output belongs to the document, not to a slide."


async def test_uploads_count_against_the_budget_of_a_document(storage_client, monkeypatch):
    first = await upload(storage_client, PNG + b"x" * 40_000)
    second = await upload(storage_client, PNG + b"x" * 40_000)
    monkeypatch.setattr(settings, "max_image_bytes", 65_536)
    slides = [
        {"template": CARD_IMAGE, "headline": "Eins", "image_upload": first},
        {"template": CARD_IMAGE, "headline": "Zwei", "image_upload": second},
    ]
    response = await storage_client.post(
        "/v1/document", headers=tenant_auth(ACME), json={"slides": slides}
    )
    assert response.status_code == 413
    assert response.json()["error"] == (
        "The images and icons of a document must be at most 64 KB together."
    )
    response = await storage_client.post(
        "/v1/document", headers=tenant_auth(ACME), json={"slides": slides[:1]}
    )
    assert response.status_code == 503


async def test_an_unknown_upload_on_a_slide_is_numbered(storage_client):
    slides = [{"template": CARD_IMAGE, "headline": "Eins", "image_upload": "up_" + "b" * 24}]
    response = await storage_client.post(
        "/v1/document", headers=tenant_auth(ACME), json={"slides": slides}
    )
    assert response.status_code == 400
    assert response.json()["error"].startswith("Slide 1: Unknown upload 'up_")


async def saved_output(storage_app, tenant: str = ACME) -> str:
    saved = await storage_app.state.files.save_output(
        tenant, "demo-card_4x5_abcdef12.png", PNG, "image/png"
    )
    return path_of(saved.url)


async def test_an_output_is_served_without_a_key(storage_client, storage_app):
    path = await saved_output(storage_app)
    response = await storage_client.get(path)
    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-length"] == str(len(PNG))
    assert response.headers["cache-control"] == "public, max-age=86400, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == (
        'inline; filename="demo-card_4x5_abcdef12.png"'
    )


async def test_head_describes_an_output_without_its_body(storage_client, storage_app):
    response = await storage_client.head(await saved_output(storage_app))
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == str(len(PNG))
    assert response.headers["content-type"] == "image/png"


async def test_an_expired_output_is_gone(storage_client, storage_app, monkeypatch):
    path = await saved_output(storage_app)
    later = files_module.now() + 86_400
    monkeypatch.setattr(files_module, "now", lambda: later)
    response = await storage_client.get(path)
    assert (response.status_code, response.json()) == (404, {"ok": False, "error": "Not found."})


async def test_an_output_is_found_under_its_tenant_only(storage_client, storage_app):
    path = await saved_output(storage_app)
    assert (
        await storage_client.get(path.replace(f"/o/{ACME}/", f"/o/{STUDIO}/"))
    ).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        f"/o/{ACME}/{'a' * 32}/demo-card_4x5_abcdef12.png",
        f"/o/{ACME}/{'a' * 32}/index.html",
        f"/o/{ACME}/{'a' * 32}",
        f"/o/{ACME}/..%2F..%2Fuploads/x.png",
    ],
)
async def test_an_unknown_output_is_not_found(storage_client, path):
    assert (await storage_client.get(path)).status_code == 404


async def test_an_unreachable_storage_is_a_503(storage_client, storage_app, storage):
    path = await saved_output(storage_app)
    ticket = (await storage_client.post("/v1/uploads", headers=tenant_auth(ACME))).json()
    storage.failing = True
    for response in (
        await storage_client.get(path),
        await storage_client.put(path_of(ticket["upload_url"]), content=PNG),
        await render(storage_client, template=CARD_IMAGE, image_upload=ticket["upload_id"]),
    ):
        assert (response.status_code, response.json()["error"]) == (503, UNAVAILABLE)
