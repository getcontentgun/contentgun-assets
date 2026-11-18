import base64
from urllib.parse import parse_qs, urlsplit

import pytest
from mcp.shared.auth import OAuthClientInformationFull

from oauth import SESSION_SECONDS, Clients, Grant
from remote_config import CLAUDE_CALLBACKS
from signing import Key
from tests.mcp_remote import (
    ADDRESS,
    CALLBACK,
    PUBLIC,
    SECRET,
    STATE,
    Clock,
    authorize,
    call,
    callback_of,
    exchange,
    pkce,
    refresh,
    register,
    remote_config,
    rewrite_tenants,
    running,
    sign_in,
    start,
    submit_address,
    submit_code,
    tool,
    txn_of,
)

DAY = 24 * 3600
EXTRA = ("https://one.example/cb", "https://two.example/cb", "https://three.example/cb")


async def tool_status(remote, token) -> int:
    return (await call(remote.client, token, "tools/list")).status_code


def error_of(response) -> str:
    assert response.status_code == 302, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["error"][0]


async def test_claude_s_registration_gets_a_short_signed_client_with_a_secret():
    async with running() as remote:
        response = await register(remote.client)
    assert response.status_code == 201
    registered = response.json()
    assert len(registered["client_id"]) < 100
    assert len(registered["client_secret"]) == 43
    assert registered["client_secret_expires_at"] == 0
    assert registered["token_endpoint_auth_method"] == "client_secret_post"
    assert registered["scope"] == "cards offline_access"
    assert registered["grant_types"] == ["authorization_code", "refresh_token"]
    assert registered["redirect_uris"] == [CALLBACK]


async def test_two_identical_registrations_get_different_clients():
    async with running() as remote:
        first = (await register(remote.client)).json()
        second = (await register(remote.client)).json()
    assert first["client_id"] != second["client_id"]
    assert first["client_secret"] != second["client_secret"]


async def test_a_client_without_a_secret_is_refused():
    async with running() as remote:
        response = await register(remote.client, token_endpoint_auth_method="none")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost:3000/callback",
        "http://127.0.0.1:3000/callback",
        "https://claude.ai/api/mcp/auth_callback/",
        "https://claude.ai/api/mcp/auth_callback?x=1",
        "https://claude.ai/api/mcp/auth_callback#x",
        "https://claude.ai/api/mcp/other",
        "http://claude.ai/api/mcp/auth_callback",
        "https://evil.example/api/mcp/auth_callback",
        "https://claude.ai.evil.example/api/mcp/auth_callback",
        "https://localhost@evil.example/cb",
        "javascript:alert(1)",
        "claude://callback",
    ],
)
async def test_a_redirect_uri_other_than_claude_s_is_refused(uri):
    async with running() as remote:
        alone = await register(remote.client, redirect_uris=[uri])
        mixed = await register(remote.client, redirect_uris=[CALLBACK, uri])
    assert alone.status_code == 400
    assert alone.json()["error"] in ("invalid_redirect_uri", "invalid_client_metadata")
    assert mixed.status_code == 400


async def test_claude_s_callback_is_taken_in_any_spelling_of_its_host():
    async with running() as remote:
        response = await register(
            remote.client, redirect_uris=["https://CLAUDE.ai:443/api/mcp/auth_callback"]
        )
    assert response.status_code == 201
    assert response.json()["redirect_uris"] == [CALLBACK]


async def test_redirect_uris_collapse_when_doubled_and_four_is_the_most():
    config = remote_config(redirect_uris=(*CLAUDE_CALLBACKS, *EXTRA))
    async with running(config) as remote:
        doubled = await register(remote.client, redirect_uris=[CALLBACK, CALLBACK])
        four = await register(remote.client, redirect_uris=list(CLAUDE_CALLBACKS + EXTRA[:2]))
        five = await register(remote.client, redirect_uris=list(CLAUDE_CALLBACKS + EXTRA))
    assert doubled.json()["redirect_uris"] == [CALLBACK]
    assert four.status_code == 201
    assert len(four.json()["client_id"]) < 100
    assert five.status_code == 400


async def test_a_client_with_basic_authentication_gets_its_tokens():
    async with running() as remote:
        registered = (
            await register(remote.client, token_endpoint_auth_method="client_secret_basic")
        ).json()
        verifier, challenge = pkce()
        txn = txn_of(await authorize(remote.client, registered["client_id"], challenge))
        await submit_address(remote.client, txn)
        callback = callback_of(await submit_code(remote.client, txn, remote.outbox.code()))
        credentials = f"{registered['client_id']}:{registered['client_secret']}"
        response = await remote.client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": callback["code"],
                "redirect_uri": CALLBACK,
                "client_id": registered["client_id"],
                "code_verifier": verifier,
            },
            headers={"Authorization": f"Basic {base64.b64encode(credentials.encode()).decode()}"},
        )
    assert response.status_code == 200, response.text


async def test_a_changed_client_id_is_unknown():
    async with running() as remote:
        client_id = (await register(remote.client)).json()["client_id"]
        body, signature = client_id.split(".")
        changed = f"{body[:5]}{'B' if body[5] == 'A' else 'A'}{body[6:]}.{signature}"
        _, challenge = pkce()
        for value in (changed, client_id[:-1], signature, "x" * 80):
            response = await authorize(remote.client, value, challenge)
            assert response.status_code == 400, value


def claude_client() -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id="placeholder",
        redirect_uris=[CALLBACK, EXTRA[0]],
        token_endpoint_auth_method="client_secret_post",
    )


def test_a_client_of_another_address_or_secret_is_unknown():
    clock = Clock()
    config = remote_config(redirect_uris=(*CLAUDE_CALLBACKS, EXTRA[0]))
    info = claude_client()
    Clients(config, clock).register(info)
    assert Clients(config, clock).find(info.client_id) is not None
    other_address = remote_config(
        public_url="https://other.test", redirect_uris=config.redirect_uris
    )
    other_secret = remote_config(secret="x" * 40, redirect_uris=config.redirect_uris)
    assert Clients(other_address, clock).find(info.client_id) is None
    assert Clients(other_secret, clock).find(info.client_id) is None


def test_a_redirect_uri_dropped_from_the_settings_drops_out_of_the_client():
    clock = Clock()
    info = claude_client()
    Clients(remote_config(redirect_uris=(*CLAUDE_CALLBACKS, EXTRA[0])), clock).register(info)
    found = Clients(remote_config(), clock).find(info.client_id)
    assert [str(uri) for uri in found.redirect_uris] == [CALLBACK]
    only_extra = OAuthClientInformationFull(
        client_id="placeholder", redirect_uris=[EXTRA[0]], token_endpoint_auth_method=None
    )
    Clients(remote_config(redirect_uris=(*CLAUDE_CALLBACKS, EXTRA[0])), clock).register(only_extra)
    assert Clients(remote_config(), clock).find(only_extra.client_id) is None


async def test_authorize_takes_this_resource_or_none_and_refuses_another():
    async with running() as remote:
        client_id = (await register(remote.client)).json()["client_id"]
        _, challenge = pkce()
        foreign = await authorize(
            remote.client, client_id, challenge, resource="https://evil.example/mcp"
        )
        slashed = await authorize(remote.client, client_id, challenge, resource=f"{PUBLIC}/mcp/")
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CALLBACK,
            "code_challenge": challenge,
            "state": STATE,
        }
        missing = await remote.client.get("/authorize", params=params)
    assert error_of(foreign) == "invalid_target"
    txn_of(slashed)
    txn_of(missing)


async def test_authorize_refuses_a_long_state_and_a_malformed_challenge():
    async with running() as remote:
        client_id = (await register(remote.client)).json()["client_id"]
        _, challenge = pkce()
        long_state = await authorize(remote.client, client_id, challenge, state="s" * 2049)
        plain = await authorize(remote.client, client_id, "plain-verifier-instead-of-a-challenge")
    assert error_of(long_state) == "invalid_request"
    assert error_of(plain) == "invalid_request"


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("cards offline_access", "cards offline_access"),
        ("cards", "cards"),
        (None, "cards offline_access"),
        ("offline_access", "cards offline_access"),
    ],
)
async def test_a_token_always_carries_cards(scope, expected):
    async with running() as remote:
        registered = (await register(remote.client)).json()
        verifier, challenge = pkce()
        response = await authorize(remote.client, registered["client_id"], challenge, scope=scope)
        txn = txn_of(response)
        await submit_address(remote.client, txn)
        callback = callback_of(await submit_code(remote.client, txn, remote.outbox.code()))
        tokens = (await exchange(remote.client, registered, callback["code"], verifier)).json()
        status = await tool_status(remote, tokens["access_token"])
    assert tokens["scope"] == expected
    assert status == 200


async def test_the_callback_carries_the_state_unchanged():
    async with running() as remote:
        _, _, txn = await start(remote)
        await submit_address(remote.client, txn)
        callback = callback_of(await submit_code(remote.client, txn, remote.outbox.code()))
    assert callback["state"] == STATE
    assert set(callback) == {"code", "state"}


async def test_an_authorization_code_works_once_for_its_client_and_verifier_only():
    async with running() as remote:
        registered, verifier, txn = await start(remote)
        other = (await register(remote.client)).json()
        await submit_address(remote.client, txn)
        code = callback_of(await submit_code(remote.client, txn, remote.outbox.code()))["code"]
        foreign = await exchange(remote.client, other, code, verifier)
        wrong = await exchange(remote.client, registered, code, pkce()[0])
        first = await exchange(remote.client, registered, code, verifier)
        again = await exchange(remote.client, registered, code, verifier)
    assert foreign.json()["error"] == "invalid_grant"
    assert wrong.json()["error"] == "invalid_grant"
    assert first.status_code == 200
    assert again.json()["error"] == "invalid_grant"


async def test_an_authorization_code_expires_after_two_minutes():
    async with running() as remote:
        registered, verifier, txn = await start(remote)
        await submit_address(remote.client, txn)
        code = callback_of(await submit_code(remote.client, txn, remote.outbox.code()))["code"]
        remote.clock.advance(121)
        response = await exchange(remote.client, registered, code, verifier)
    assert response.json()["error"] == "invalid_grant"


async def test_an_access_token_works_for_an_hour_and_not_after():
    async with running() as remote:
        signed_in = await sign_in(remote)
        assert signed_in.tokens["expires_in"] == 3600
        remote.clock.advance(3599)
        before = await tool_status(remote, signed_in.access)
        remote.clock.advance(2)
        after = await tool_status(remote, signed_in.access)
    assert (before, after) == (200, 401)


async def test_a_refresh_gives_a_new_pair_and_the_old_refresh_token_stays_valid():
    async with running() as remote:
        signed_in = await sign_in(remote)
        remote.clock.advance(3601)
        first = await refresh(
            remote.client, signed_in.registered, signed_in.tokens["refresh_token"]
        )
        remote.clock.advance(1)
        second = await refresh(
            remote.client, signed_in.registered, signed_in.tokens["refresh_token"]
        )
        status = await tool_status(remote, first.json()["access_token"])
    assert first.status_code == 200
    assert first.json()["refresh_token"] != signed_in.tokens["refresh_token"]
    assert second.status_code == 200
    assert status == 200


async def test_a_refresh_token_works_for_its_own_client_only():
    async with running() as remote:
        signed_in = await sign_in(remote)
        other = (await register(remote.client)).json()
        response = await refresh(remote.client, other, signed_in.tokens["refresh_token"])
    assert response.json()["error"] == "invalid_grant"


async def test_a_refresh_may_narrow_the_scopes_but_not_widen_them():
    async with running() as remote:
        signed_in = await sign_in(remote)
        registered, token = signed_in.registered, signed_in.tokens["refresh_token"]
        narrow = await refresh(remote.client, registered, token, scope="cards")
        wide = await refresh(remote.client, registered, token, scope="cards admin")
    assert narrow.json()["scope"] == "cards"
    assert wide.json()["error"] == "invalid_scope"


async def test_tokens_do_not_trade_roles():
    async with running() as remote:
        signed_in = await sign_in(remote)
        bearer_refresh = await tool_status(remote, signed_in.tokens["refresh_token"])
        refresh_access = await refresh(remote.client, signed_in.registered, signed_in.access)
        bearer_secret = await tool_status(remote, signed_in.registered["client_secret"])
    assert bearer_refresh == 401
    assert refresh_access.json()["error"] == "invalid_grant"
    assert bearer_secret == 401


async def test_a_refresh_token_expires_after_30_days():
    async with running() as remote:
        signed_in = await sign_in(remote)
        remote.clock.advance(30 * DAY + 1)
        response = await refresh(
            remote.client, signed_in.registered, signed_in.tokens["refresh_token"]
        )
    assert response.json()["error"] == "invalid_grant"


async def test_a_sign_in_lasts_90_days_however_often_it_is_renewed():
    async with running() as remote:
        signed_in = await sign_in(remote)
        token = signed_in.tokens["refresh_token"]
        for _ in range(3):
            remote.clock.advance(29 * DAY)
            renewed = await refresh(remote.client, signed_in.registered, token)
            assert renewed.status_code == 200
            token = renewed.json()["refresh_token"]
        remote.clock.advance(3 * DAY - 600)
        last = await refresh(remote.client, signed_in.registered, token)
        remote.clock.advance(600)
        over = await refresh(remote.client, signed_in.registered, last.json()["refresh_token"])
    assert last.json()["expires_in"] == 600
    assert over.json()["error"] == "invalid_grant"
    assert SESSION_SECONDS == 90 * DAY


async def test_sessions_after_signs_out_every_earlier_sign_in():
    clock = Clock()
    config = remote_config(sessions_after=int(clock.now) + 10)
    async with running(config, clock=clock) as remote:
        early = await sign_in(remote)
        status = await tool_status(remote, early.access)
        renewed = await refresh(remote.client, early.registered, early.tokens["refresh_token"])
        clock.advance(11)
        late = await sign_in(remote)
        late_status = await tool_status(remote, late.access)
    assert status == 401
    assert renewed.json()["error"] == "invalid_grant"
    assert late_status == 200


async def test_an_address_taken_out_of_the_tenants_file_loses_access_at_once(tmp_path):
    path = rewrite_tenants(tmp_path / "tenants.toml", {"acme": {"emails": [ADDRESS]}})
    async with running(remote_config(tenants_file=path, emails=())) as remote:
        signed_in = await sign_in(remote)
        result = await tool(remote.client, signed_in.access, "whoami")
        rewrite_tenants(path, {"acme": {"emails": ["other@acme.example"]}})
        status = await tool_status(remote, signed_in.access)
        renewed = await refresh(
            remote.client, signed_in.registered, signed_in.tokens["refresh_token"]
        )
    assert result["structuredContent"]["tenant"] == "acme"
    assert status == 401
    assert renewed.json()["error"] == "invalid_grant"


async def test_an_address_moved_to_another_tenant_has_to_sign_in_again(tmp_path):
    path = rewrite_tenants(tmp_path / "tenants.toml", {"acme": {"emails": [ADDRESS]}})
    async with running(remote_config(tenants_file=path, emails=())) as remote:
        signed_in = await sign_in(remote)
        rewrite_tenants(path, {"studio": {"emails": [ADDRESS]}})
        status = await tool_status(remote, signed_in.access)
        again = await sign_in(remote)
        result = await tool(remote.client, again.access, "whoami")
    assert status == 401
    assert result["structuredContent"]["tenant"] == "studio"


async def test_an_address_removed_between_code_and_token_gets_no_token(tmp_path):
    path = rewrite_tenants(tmp_path / "tenants.toml", {"acme": {"emails": [ADDRESS]}})
    async with running(remote_config(tenants_file=path, emails=())) as remote:
        registered, verifier, txn = await start(remote)
        await submit_address(remote.client, txn)
        code = callback_of(await submit_code(remote.client, txn, remote.outbox.code()))["code"]
        rewrite_tenants(path, {"acme": {"emails": ["other@acme.example"]}})
        response = await exchange(remote.client, registered, code, verifier)
    assert response.json()["error"] == "invalid_grant"


async def test_a_new_secret_ends_every_client_and_token():
    clock = Clock()
    async with running(clock=clock) as remote:
        signed_in = await sign_in(remote)
    async with running(
        remote_config(secret="another-secret-of-the-tests-0123456789"), clock=clock
    ) as remote:
        status = await tool_status(remote, signed_in.access)
        renewed = await refresh(
            remote.client, signed_in.registered, signed_in.tokens["refresh_token"]
        )
    assert status == 401
    assert renewed.status_code == 401
    assert renewed.json()["error"] == "invalid_client"


async def test_a_token_of_another_server_with_the_same_secret_is_refused():
    clock = Clock()
    async with running(clock=clock) as remote:
        signed_in = await sign_in(remote)
    async with running(remote_config(public_url="https://other.test"), clock=clock) as remote:
        status = await tool_status(remote, signed_in.access)
    assert status == 401


async def test_a_restarted_server_takes_the_tokens_it_issued_before():
    clock = Clock()
    async with running(clock=clock) as remote:
        signed_in = await sign_in(remote)
    async with running(clock=clock) as remote:
        status = await tool_status(remote, signed_in.access)
        renewed = await refresh(
            remote.client, signed_in.registered, signed_in.tokens["refresh_token"]
        )
    assert status == 200
    assert renewed.status_code == 200


def test_a_key_opens_what_it_sealed_and_nothing_else():
    key = Key(SECRET, PUBLIC, "access token")
    token = key.seal({"e": ADDRESS})
    body, signature = token.split(".")
    assert key.open(token) == {"e": ADDRESS}
    assert key.open(f"{body}x.{signature}") is None
    flipped = "B" if signature[-1] == "A" else "A"
    assert key.open(f"{body}.{signature[:-1]}{flipped}") is None
    assert Key(SECRET, PUBLIC, "refresh token").open(token) is None
    assert Key(SECRET, "https://other.test", "access token").open(token) is None
    assert Key("y" * 40, PUBLIC, "access token").open(token) is None
    assert key.open(None) is None
    assert key.open("a" * 3000 + "." + signature) is None
    assert key.open(key.sign(b"[1, 2]")) is None
    assert key.open(key.sign(b"not json")) is None


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"e": ADDRESS, "t": "acme", "c": "x", "s": "cards", "l": True, "x": 1},
        {"e": ADDRESS, "t": "acme", "c": "x", "s": "cards", "l": 1, "x": "1"},
        {"e": 1, "t": "acme", "c": "x", "s": "cards", "l": 1, "x": 1},
        {"e": ADDRESS, "t": "acme", "c": "x", "s": ["cards"], "l": 1, "x": 1},
    ],
)
def test_a_grant_with_a_missing_or_mistyped_claim_is_none(claims):
    assert Grant.read(claims) is None
