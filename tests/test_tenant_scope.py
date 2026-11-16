import html
import re

import pytest

from tests.conftest import (
    ACME,
    ACME_CARD,
    AUTH,
    BUNDLE,
    CARD,
    INTERNAL_KEY,
    SOLO,
    SOLO_CARD,
    STUDIO,
    STUDIO_CARD,
    bearer,
    tenant_auth,
)

HEADER = "X-Contentgun-Tenant"


async def template_ids(client, headers: dict[str, str]) -> list[str]:
    response = await client.get("/v1/templates", headers=headers)
    assert response.status_code == 200, response.text
    return [template["id"] for template in response.json()["templates"]]


def available(app, tenant: str) -> str:
    return ", ".join(app.state.tenants.get(tenant).registry.ids)


async def render(client, tenant: str, **payload):
    body = {"headline": "Hallo"} | payload
    return await client.post("/v1/render", headers=tenant_auth(tenant), json=body)


async def test_a_tenant_lists_its_own_and_the_built_in_templates(tenant_client, tenant_app):
    builtin = tenant_app.state.tenants.builtin_ids
    assert await template_ids(tenant_client, tenant_auth(ACME)) == sorted([*builtin, ACME_CARD])
    assert await template_ids(tenant_client, tenant_auth(STUDIO)) == sorted([*builtin, STUDIO_CARD])


async def test_a_tenant_with_a_bundle_list_lists_only_those(tenant_client):
    response = await tenant_client.get("/v1/templates", headers=tenant_auth(SOLO))
    assert [template["id"] for template in response.json()["templates"]] == [SOLO_CARD]
    assert [bundle["name"] for bundle in response.json()["bundles"]] == ["solo-cards"]


@pytest.mark.parametrize(
    ("tenant", "template"),
    [(ACME, ACME_CARD), (ACME, CARD), (STUDIO, STUDIO_CARD), (STUDIO, CARD), (SOLO, SOLO_CARD)],
)
async def test_a_tenant_reaches_the_renderer_with_its_templates(tenant_client, tenant, template):
    response = await render(tenant_client, tenant, template=template)
    assert response.status_code == 503
    assert response.json()["error"] == "Renderer is not available."


@pytest.mark.parametrize(
    ("tenant", "template"),
    [(ACME, STUDIO_CARD), (STUDIO, ACME_CARD), (ACME, SOLO_CARD), (SOLO, CARD)],
)
async def test_a_template_of_another_tenant_is_unknown_and_named_nowhere(
    tenant_client, tenant_app, tenant, template
):
    response = await render(tenant_client, tenant, template=template)
    assert response.status_code == 400
    assert response.json()["error"] == (
        f"Unknown template {template!r}. Available: {available(tenant_app, tenant)}."
    )


async def test_the_default_template_follows_the_tenant(tenant_client):
    response = await render(tenant_client, SOLO, nope="x")
    assert f"'nope' is not accepted by template {SOLO_CARD!r}" in response.json()["error"]
    response = await render(tenant_client, ACME, nope="x")
    assert f"'nope' is not accepted by template {CARD!r}" in response.json()["error"]


async def test_a_document_slide_of_another_tenant_is_unknown(tenant_client, tenant_app):
    slides = [
        {"template": ACME_CARD, "headline": "Hallo"},
        {"template": STUDIO_CARD, "headline": "Hallo"},
    ]
    response = await tenant_client.post(
        "/v1/document", headers=tenant_auth(ACME), json={"slides": slides}
    )
    assert response.status_code == 400
    assert response.json()["error"] == (
        f"Slide 2: Unknown template {STUDIO_CARD!r}. Available: {available(tenant_app, ACME)}."
    )


async def test_a_key_of_the_one_tenant_is_no_key_here(tenant_client):
    assert (await tenant_client.get("/v1/templates", headers=AUTH)).status_code == 401


async def test_the_internal_key_acts_for_the_tenant_in_the_header(tenant_client):
    ids = await template_ids(tenant_client, bearer(INTERNAL_KEY) | {HEADER: STUDIO})
    assert STUDIO_CARD in ids
    assert ACME_CARD not in ids


async def test_the_internal_key_renders_with_the_default_of_its_tenant(tenant_client):
    response = await tenant_client.post(
        "/v1/render",
        headers=bearer(INTERNAL_KEY) | {HEADER: SOLO},
        json={"headline": "Hallo", "nope": "x"},
    )
    assert f"is not accepted by template {SOLO_CARD!r}" in response.json()["error"]


async def test_the_internal_key_needs_the_header(tenant_client):
    response = await tenant_client.get("/v1/templates", headers=bearer(INTERNAL_KEY))
    assert response.status_code == 400
    assert response.json()["error"] == "The internal key needs the header X-Contentgun-Tenant."


async def test_the_internal_key_with_an_unknown_tenant_is_refused(tenant_client):
    response = await tenant_client.get(
        "/v1/templates", headers=bearer(INTERNAL_KEY) | {HEADER: "nobody"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "The header X-Contentgun-Tenant names no known tenant."


async def test_a_tenant_key_ignores_the_header(tenant_client):
    ids = await template_ids(tenant_client, tenant_auth(ACME) | {HEADER: STUDIO})
    assert ACME_CARD in ids
    assert STUDIO_CARD not in ids


async def test_without_an_internal_key_the_header_is_no_way_in(client):
    response = await client.get("/v1/templates", headers=bearer(INTERNAL_KEY) | {HEADER: "default"})
    assert response.status_code == 401


async def test_the_debug_overview_lists_the_tenants(tenant_client, tenant_app):
    response = await tenant_client.get("/v1/debug")
    assert response.status_code == 200
    for name in tenant_app.state.tenants.names:
        assert f'href="/v1/debug?tenant={name}"' in response.text


async def test_the_debug_overview_of_a_tenant_lists_its_templates_only(tenant_client):
    response = await tenant_client.get("/v1/debug", params={"tenant": ACME})
    assert response.status_code == 200
    assert "<h1>Templates of tenant acme</h1>" in response.text
    assert f"<h2>Bundle {BUNDLE}</h2>" in response.text
    assert "<h2>Bundle acme-brand</h2>" in response.text
    assert "studio-cards" not in response.text


async def test_every_preview_link_of_a_tenant_overview_works(tenant_client):
    response = await tenant_client.get("/v1/debug", params={"tenant": ACME})
    links = [html.unescape(href) for href in re.findall(r'href="([^"]+)"', response.text)]
    previews = [link for link in links if link.startswith("/v1/debug/html")]
    assert previews
    for link in previews:
        assert link.startswith(f"/v1/debug/html?tenant={ACME}&")
        assert (await tenant_client.get(link)).status_code == 200, link


async def test_the_debug_preview_needs_a_tenant_when_there_are_several(tenant_client):
    response = await tenant_client.get("/v1/debug/html", params={"template": CARD})
    assert response.status_code == 400
    assert response.json()["error"] == (
        "Name a tenant with the parameter tenant. Tenants: acme, solo, studio."
    )


async def test_the_debug_preview_refuses_an_unknown_tenant(tenant_client):
    response = await tenant_client.get("/v1/debug/html", params={"tenant": "nobody"})
    assert response.status_code == 400
    assert response.json()["error"].startswith("Unknown tenant 'nobody'. Tenants: ")


async def test_the_debug_preview_keeps_to_the_templates_of_its_tenant(tenant_client):
    response = await tenant_client.get("/v1/debug/html", params={"tenant": SOLO, "template": CARD})
    assert response.status_code == 400
    assert response.json()["error"] == f"Unknown template {CARD!r}. Available: {SOLO_CARD}."
    assert (await tenant_client.get("/v1/debug/html", params={"tenant": SOLO})).status_code == 200
