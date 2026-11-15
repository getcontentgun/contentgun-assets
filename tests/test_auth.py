from tests.conftest import AUTH


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


async def test_unconfigured_keys_return_500(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "api_keys", "")
    response = await client.get("/v1/templates", headers=AUTH)
    assert response.status_code == 500
    assert response.json()["error"] == "API keys are not configured on the server."


async def test_health_does_not_require_a_key(client):
    assert (await client.get("/health")).status_code != 401
