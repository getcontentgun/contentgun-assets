import pytest

from app.main import create_app
from tests.conftest import AUTH, client_of, tenants_for


async def test_missing_key_is_rejected(client):
    response = await client.get("/v1/templates")
    assert response.status_code == 401
    assert response.json() == {"ok": False, "error": "Unauthorized."}
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_wrong_key_is_rejected(client):
    response = await client.get("/v1/templates", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


async def test_non_bearer_scheme_is_rejected(client):
    response = await client.get("/v1/templates", headers={"Authorization": "ApiKey test-key"})
    assert response.status_code == 401


async def test_query_parameter_key_is_rejected(client):
    response = await client.get("/v1/templates?key=test-key")
    assert response.status_code == 401


async def test_valid_key_is_accepted(client):
    response = await client.get("/v1/templates", headers=AUTH)
    assert response.status_code == 200


async def test_second_configured_key_is_accepted(client):
    response = await client.get("/v1/templates", headers={"Authorization": "Bearer second-key"})
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/v1/templates", "/v1/render", "/v1/document"])
async def test_without_any_key_every_authenticated_route_answers_500(path):
    async with client_of(create_app(tenants_for(api_keys=()))) as client:
        if path == "/v1/templates":
            response = await client.get(path, headers=AUTH)
        else:
            response = await client.post(path, headers=AUTH, json={})
    assert response.status_code == 500
    assert response.json()["error"] == "API keys are not configured on the server."


async def test_health_does_not_require_a_key(client):
    assert (await client.get("/health")).status_code != 401
