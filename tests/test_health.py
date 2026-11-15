from tests.conftest import AUTH, CARD


async def test_health_reports_503_without_a_browser(client):
    response = await client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"ok": False, "error": "Browser not ready."}


async def test_health_sets_request_id(client):
    response = await client.get("/health")
    assert len(response.headers["X-Request-Id"]) == 36


async def test_health_needs_no_key(client):
    assert (await client.get("/health")).status_code != 401


async def test_templates_are_loaded(client):
    body = (await client.get("/v1/templates", headers=AUTH)).json()
    assert body["templates"][0]["id"] == CARD


async def test_unknown_path_returns_envelope(client):
    response = await client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"ok": False, "error": "Not found."}
