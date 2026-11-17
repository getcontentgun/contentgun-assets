import asyncio
import base64
import ipaddress
import logging
import ssl

import httpx
import pytest

from app import fetching
from app.config import settings
from app.errors import ApiError
from app.fetching import Budget, OverBudget, RemoteImages, allowed
from tests.conftest import SQUARE_SVG, image_base64
from tests.fake_web import CDN_V4, PUBLIC_V4, PUBLIC_V6, FakeWeb, redirect, serve, streamed

PNG = base64.b64decode(image_base64())
SVG = SQUARE_SVG.encode("utf-8")
HOST = "images.example"
URL = f"https://{HOST}/photo.png"
NOT_ALLOWED = "The image URL points to an address that is not allowed."
NOT_FETCHED = "The image URL could not be fetched."

# One address from every range a URL must not reach, the cloud metadata addresses included.
FORBIDDEN = [
    "127.0.0.1",
    "127.255.255.254",
    "10.0.0.1",
    "10.255.255.254",
    "172.16.0.1",
    "172.31.255.254",
    "192.168.0.1",
    "192.168.255.254",
    "169.254.0.1",
    "169.254.169.254",
    "100.64.0.1",
    "100.100.100.200",
    "100.127.255.254",
    "192.0.0.192",
    "168.63.129.16",
    "0.0.0.0",
    "0.255.255.255",
    "224.0.0.1",
    "239.255.255.250",
    "240.0.0.1",
    "255.255.255.255",
    "192.0.2.1",
    "198.51.100.7",
    "203.0.113.9",
    "198.18.0.1",
    "::1",
    "::",
    "fe80::1",
    "febf::1",
    "fec0::1",
    "fc00::1",
    "fd00:ec2::254",
    "ff02::1",
    "ff0e::1",
    "2001:db8::1",
    "3fff::1",
    "100::1",
    "::ffff:127.0.0.1",
    "::ffff:169.254.169.254",
    "::ffff:8.8.8.8",
    "::7f00:1",
    "2002:7f00:1::1",
    "2002:808:808::1",
    "2001:0:4136:e378:8000:63bf:3fff:fdd2",
    "64:ff9b::7f00:1",
    "64:ff9b::808:808",
    "64:ff9b:1::1",
]


def literal_url(address: str) -> str:
    host = f"[{address}]" if ":" in address else address
    return f"https://{host}/photo.png"


@pytest.fixture
def web() -> FakeWeb:
    web = FakeWeb()
    web.add_host(HOST, PUBLIC_V4)
    web.pages[URL] = serve(PNG)
    return web


async def fetch(web: FakeWeb, url: str = URL, kind: str = "image", budget=None) -> bytes:
    return await web.remote().fetch(url, kind, budget)


async def failure(web: FakeWeb, url: str = URL, kind: str = "image") -> ApiError:
    with pytest.raises(ApiError) as excinfo:
        await fetch(web, url, kind)
    return excinfo.value


async def test_an_image_is_fetched_from_the_checked_address(web):
    assert await fetch(web) == PNG
    assert web.lookups == [HOST]
    (request,) = web.requests
    assert request.url.host == PUBLIC_V4
    assert request.headers["host"] == HOST
    assert request.extensions["sni_hostname"] == HOST
    assert request.headers["accept-encoding"] == "identity"
    assert request.headers["user-agent"].startswith("contentgun-assets/")


async def test_an_icon_asks_for_an_svg(web):
    web.pages[f"https://{HOST}/icon.svg"] = serve(SVG)
    assert await fetch(web, f"https://{HOST}/icon.svg", "icon") == SVG
    assert web.requests[0].headers["accept"] == "image/svg+xml"


async def test_an_ipv6_address_is_reached_in_brackets(web):
    web.add_host(HOST, PUBLIC_V6)
    assert await fetch(web) == PNG
    (request,) = web.requests
    assert str(request.url) == f"https://[{PUBLIC_V6}]/photo.png"
    assert request.headers["host"] == HOST


async def test_an_address_in_the_url_needs_no_lookup(web):
    web.pages[literal_url(PUBLIC_V4)] = serve(PNG)
    assert await fetch(web, literal_url(PUBLIC_V4)) == PNG
    assert web.lookups == []
    assert web.requests[0].extensions["sni_hostname"] == PUBLIC_V4


@pytest.mark.parametrize("address", [PUBLIC_V4, PUBLIC_V6, CDN_V4, "8.8.8.8", "2a00:1450::1"])
def test_a_global_address_is_allowed(address):
    assert allowed(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", FORBIDDEN)
async def test_a_forbidden_address_in_the_url_is_refused(web, address):
    error = await failure(web, literal_url(address))
    assert (error.status_code, error.message) == (400, NOT_ALLOWED)
    assert web.lookups == []
    assert web.requests == []


@pytest.mark.parametrize("address", FORBIDDEN)
async def test_a_name_that_resolves_to_a_forbidden_address_is_refused(web, address):
    web.add_host("inside.example", address)
    error = await failure(web, "https://inside.example/photo.png")
    assert (error.status_code, error.message) == (400, NOT_ALLOWED)
    assert web.requests == []


@pytest.mark.parametrize(
    "addresses", [(PUBLIC_V4, "10.0.0.5"), ("10.0.0.5", PUBLIC_V4), (PUBLIC_V6, "::1")]
)
async def test_one_forbidden_address_among_public_ones_refuses_the_url(web, addresses):
    web.add_host(HOST, *addresses)
    error = await failure(web)
    assert (error.status_code, error.message) == (400, NOT_ALLOWED)
    assert web.requests == []


async def test_a_second_answer_of_the_resolver_never_decides_where_the_request_goes(web):
    web.answer(HOST, [PUBLIC_V4], ["127.0.0.1"])
    assert await fetch(web) == PNG
    assert web.lookups == [HOST]
    (request,) = web.requests
    assert request.url.host == PUBLIC_V4
    assert request.headers["host"] == HOST
    assert request.extensions["sni_hostname"] == HOST


@pytest.mark.parametrize(
    "url",
    [
        f"http://{HOST}/photo.png",
        f"https://{HOST}:8443/photo.png",
        f"https://{HOST}:80/photo.png",
        f"https://user:secret@{HOST}/photo.png",
        f"https://user@{HOST}/photo.png",
        f"ftp://{HOST}/photo.png",
        "file:///etc/passwd",
        f"{HOST}/photo.png",
        f"//{HOST}/photo.png",
        "https:///photo.png",
        "https://exa mple.example/photo.png",
        "https://images\n.example/photo.png",
        "not a url",
    ],
)
async def test_a_url_against_the_rules_is_refused_without_a_lookup(web, url):
    error = await failure(web, url)
    assert (error.status_code, error.message) == (400, NOT_ALLOWED)
    assert web.lookups == []


async def test_the_explicit_https_port_is_allowed(web):
    assert await fetch(web, f"https://{HOST}:443/photo.png") == PNG


@pytest.mark.parametrize(
    "location",
    [
        "https://inside.example/photo.png",
        "https://127.0.0.1/photo.png",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::ffff:10.0.0.1]/photo.png",
        f"http://{HOST}/photo.png",
        f"https://{HOST}:8443/photo.png",
        f"https://user:secret@{HOST}/photo.png",
    ],
)
async def test_a_redirect_against_the_rules_is_refused(web, location):
    web.add_host("inside.example", "10.0.0.5")
    web.pages[URL] = redirect(location)
    error = await failure(web)
    assert (error.status_code, error.message) == (400, NOT_ALLOWED)
    assert len(web.requests) == 1


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_a_redirect_is_followed_with_a_lookup_of_its_own(web, status):
    web.add_host("cdn.example", CDN_V4)
    web.pages[URL] = redirect("https://cdn.example/photo.png", status)
    web.pages["https://cdn.example/photo.png"] = serve(PNG)
    assert await fetch(web) == PNG
    assert web.lookups == [HOST, "cdn.example"]
    assert web.requests[1].url.host == CDN_V4
    assert web.requests[1].extensions["sni_hostname"] == "cdn.example"


async def test_a_relative_redirect_is_resolved_against_the_host_name(web):
    web.pages[URL] = redirect("/other.png")
    web.pages[f"https://{HOST}/other.png"] = serve(PNG)
    assert await fetch(web) == PNG
    assert web.lookups == [HOST, HOST]
    assert web.requests[1].headers["host"] == HOST


async def test_every_hop_checks_the_addresses_again(web):
    web.answer(HOST, [PUBLIC_V4], ["10.0.0.1"])
    web.pages[URL] = redirect("/other.png")
    error = await failure(web)
    assert (error.status_code, error.message) == (400, NOT_ALLOWED)
    assert len(web.requests) == 1


def chain(web: FakeWeb, redirects: int) -> None:
    for hop in range(redirects):
        web.pages[f"https://{HOST}/{hop}.png"] = redirect(f"/{hop + 1}.png")
    web.pages[f"https://{HOST}/{redirects}.png"] = serve(PNG)


async def test_three_redirects_are_followed(web):
    chain(web, 3)
    assert await fetch(web, f"https://{HOST}/0.png") == PNG


async def test_a_fourth_redirect_is_not_followed(web):
    chain(web, 4)
    error = await failure(web, f"https://{HOST}/0.png")
    assert (error.status_code, error.message) == (502, NOT_FETCHED)
    assert len(web.requests) == 4


async def test_a_redirect_without_a_location_fails(web):
    web.pages[URL] = lambda request: httpx.Response(302)
    error = await failure(web)
    assert (error.status_code, error.message) == (502, NOT_FETCHED)


@pytest.mark.parametrize("status", [204, 206, 304, 403, 404, 500, 503])
async def test_an_answer_other_than_200_fails(web, status):
    web.pages[URL] = serve(PNG, status=status)
    error = await failure(web)
    assert (error.status_code, error.message) == (502, NOT_FETCHED)


async def test_an_unknown_host_fails(web):
    error = await failure(web, "https://nowhere.example/photo.png")
    assert (error.status_code, error.message) == (502, NOT_FETCHED)


async def test_a_lookup_without_addresses_fails(web):
    web.add_host(HOST)
    error = await failure(web)
    assert (error.status_code, error.message) == (502, NOT_FETCHED)


@pytest.mark.parametrize(
    "exception",
    [
        httpx.ConnectError("certificate verify failed"),
        httpx.ConnectTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.RemoteProtocolError("malformed"),
    ],
)
async def test_a_failing_connection_fails(web, exception):
    def broken(request):
        raise exception

    web.pages[URL] = broken
    error = await failure(web)
    assert (error.status_code, error.message) == (502, NOT_FETCHED)


async def test_the_content_type_of_the_other_side_does_not_count(web):
    web.pages[URL] = serve(PNG, headers={"Content-Type": "text/html"})
    assert await fetch(web) == PNG


@pytest.fixture
def small_limit(monkeypatch):
    monkeypatch.setattr(settings, "max_image_bytes", 65_536)


async def test_a_body_over_the_limit_is_refused(web, small_limit):
    web.pages[URL] = serve(PNG + b"x" * 70_000)
    error = await failure(web)
    assert (error.status_code, error.message) == (
        413,
        "The file at 'image_url' must be at most 64 KB.",
    )


async def test_a_declared_length_over_the_limit_is_refused_before_the_body(web, small_limit):
    read = []

    async def body():
        read.append(True)
        yield PNG

    web.pages[URL] = streamed(body, headers={"Content-Length": "70000"})
    error = await failure(web)
    assert error.status_code == 413
    assert read == []


async def test_a_body_without_a_length_is_counted(web, small_limit):
    async def body():
        for _ in range(3):
            yield b"x" * 30_000

    web.pages[URL] = streamed(body)
    assert (await failure(web)).status_code == 413


async def test_a_body_longer_than_its_declared_length_is_counted(web, small_limit):
    async def body():
        for _ in range(3):
            yield b"x" * 30_000

    web.pages[URL] = streamed(body, headers={"Content-Length": "100"})
    assert (await failure(web)).status_code == 413


async def test_an_endless_body_is_cut_off_at_the_limit(web, small_limit):
    sent = []

    async def endless():
        while True:
            sent.append(16_384)
            yield b"x" * 16_384

    web.pages[URL] = streamed(endless)
    assert (await failure(web)).status_code == 413
    assert sum(sent) <= 65_536 + 16_384


async def test_an_icon_is_cut_off_at_its_own_limit(web):
    web.pages[f"https://{HOST}/icon.svg"] = serve(b"<svg>" + b" " * 270_000)
    error = await failure(web, f"https://{HOST}/icon.svg", "icon")
    assert (error.status_code, error.message) == (
        413,
        "The file at 'icon_url' must be at most 256 KB.",
    )


async def test_a_trickling_body_fails_at_the_time_limit(web, monkeypatch):
    monkeypatch.setattr(fetching, "TIMEOUT_SECONDS", 0.3)

    async def trickle():
        while True:
            yield b"x"
            await asyncio.sleep(0.05)

    web.pages[URL] = streamed(trickle)
    error = await failure(web)
    assert (error.status_code, error.message) == (502, NOT_FETCHED)


async def test_the_time_limit_covers_the_redirects(web, monkeypatch):
    monkeypatch.setattr(fetching, "TIMEOUT_SECONDS", 0.3)

    async def slow(request):
        await asyncio.sleep(0.2)
        return httpx.Response(302, headers={"Location": "/again.png"})

    web.pages[URL] = slow
    web.pages[f"https://{HOST}/again.png"] = slow
    error = await failure(web)
    assert (error.status_code, error.message) == (502, NOT_FETCHED)
    assert len(web.requests) == 2


async def test_a_shared_budget_stops_the_fetch_that_exceeds_it(web):
    budget = Budget(len(PNG) + 10, "The document is over its budget.")
    assert await fetch(web, budget=budget) == PNG
    with pytest.raises(OverBudget) as excinfo:
        await fetch(web, budget=budget)
    assert (excinfo.value.status_code, excinfo.value.message) == (
        413,
        "The document is over its budget.",
    )


async def test_no_url_and_no_address_is_logged(web, caplog):
    caplog.set_level(logging.INFO, logger="app.fetching")
    web.add_host("inside.example", "10.0.0.5")
    await fetch(web)
    await failure(web, "https://inside.example/photo.png")
    web.pages[URL] = serve(PNG, status=404)
    await failure(web)
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert f"bytes={len(PNG)}" in logged
    assert "status 404" in logged
    for secret in (HOST, "inside.example", PUBLIC_V4, "10.0.0.5", "photo.png"):
        assert secret not in logged


async def test_the_client_verifies_tls_and_ignores_the_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    remote = RemoteImages()
    assert remote._tls.verify_mode == ssl.CERT_REQUIRED
    assert remote._tls.check_hostname
    client = remote._client()
    try:
        assert client.trust_env is False
        assert client.follow_redirects is False
        assert not client._mounts
    finally:
        await client.aclose()
