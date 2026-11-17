import asyncio
import ipaddress
import logging
import re
import socket
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import cached_property

import httpx

from app import __version__
from app.config import settings
from app.errors import ApiError
from app.files import Kind
from app.images import icon_limit, readable_size

logger = logging.getLogger(__name__)
# httpx logs every request with its URL at INFO, and the URL of a caller may carry a token.
logging.getLogger("httpx").setLevel(logging.WARNING)

TIMEOUT_SECONDS = 10
MAX_REDIRECTS = 3
CONCURRENCY = 4
PORT = 443
REDIRECTS = frozenset({301, 302, 303, 307, 308})
HOST = re.compile(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*\.?")
ACCEPT = {"image": "image/png, image/jpeg, image/webp", "icon": "image/svg+xml"}
USER_AGENT = f"contentgun-assets/{__version__}"

# Every range is listed even where is_global already excludes it: Python 3.13 counts multicast,
# NAT64, IPv4-compatible and site-local addresses as global, and Python 3.12 judges IPv4-mapped
# addresses by their IPv6 form.
FORBIDDEN = tuple(
    ipaddress.ip_network(network)
    for network in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        # Azure's host address: public, yet reachable from every virtual machine there.
        "168.63.129.16/32",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/96",
        "::ffff:0:0/96",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "100::/64",
        "2001::/32",
        "2001:db8::/32",
        "2002::/16",
        "3fff::/20",
        "fc00::/7",
        "fe80::/10",
        "fec0::/10",
        "ff00::/8",
    )
)

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str], Awaitable[list[str]]]


def url_subject(kind: Kind) -> str:
    return f"The file at '{kind}_url'"


def _refused(kind: Kind) -> ApiError:
    logger.warning("The %s URL was refused: it points to an address that is not allowed", kind)
    return ApiError(400, f"The {kind} URL points to an address that is not allowed.")


def _failed(kind: Kind, reason: str) -> ApiError:
    logger.warning("The %s URL could not be fetched: %s", kind, reason)
    return ApiError(502, f"The {kind} URL could not be fetched.")


def _too_large(kind: Kind, limit: int) -> ApiError:
    return ApiError(413, f"{url_subject(kind)} must be at most {readable_size(limit)}.")


class OverBudget(ApiError):
    """The URLs of a document brought in more than what its budget had left."""


class Budget:
    """The bytes the URLs of one document may still bring in, shared by all of its fetches."""

    def __init__(self, remaining: int, message: str):
        self.remaining = remaining
        self._message = message

    def spend(self, size: int) -> None:
        self.remaining -= size
        if self.remaining < 0:
            raise OverBudget(413, self._message)


@dataclass(frozen=True)
class Redirect:
    location: str


def allowed(address: Address) -> bool:
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not any(address in network for network in FORBIDDEN)
    )


def _literal(host: str) -> Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def checked_url(url: str | httpx.URL, kind: Kind) -> httpx.URL:
    """The URL, once it uses https on port 443, names a host and carries no credentials."""
    try:
        parsed = httpx.URL(url.strip()) if isinstance(url, str) else url
        host = parsed.raw_host.decode("ascii")
    except (httpx.InvalidURL, ValueError):
        raise _refused(kind) from None
    if (
        parsed.scheme != "https"
        or parsed.port not in (None, PORT)
        or parsed.userinfo
        or not (HOST.fullmatch(host) or _literal(host))
    ):
        raise _refused(kind)
    return parsed


async def system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    found = await loop.getaddrinfo(host, PORT, type=socket.SOCK_STREAM, flags=socket.AI_ADDRCONFIG)
    return list(dict.fromkeys(str(sockaddr[0]) for *_, sockaddr in found))


async def _body(
    response: httpx.Response, kind: Kind, limit: int, budget: Budget | None, uses: int
) -> bytes:
    declared = response.headers.get("content-length", "")
    if declared.isascii() and declared.isdigit() and int(declared) > limit:
        raise _too_large(kind, limit)
    body = bytearray()
    # Raw on purpose: decoded chunks of a compressed body would grow before the limit sees them.
    async for chunk in response.aiter_raw():
        body += chunk
        if len(body) > limit:
            raise _too_large(kind, limit)
        if budget is not None:
            budget.spend(len(chunk) * uses)
    return bytes(body)


class RemoteImages:
    """Fetches the image or the icon a request names by URL. Every hop resolves its host once,
    refuses the URL when any of the addresses is not allowed and connects to the first of them,
    with the host name in the Host header and in TLS, so no second resolution decides where the
    request goes."""

    def __init__(
        self,
        resolve: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._resolve = resolve
        self._transport = transport

    @cached_property
    def _tls(self) -> ssl.SSLContext:
        return httpx.create_ssl_context(trust_env=False)

    async def fetch(
        self, url: str, kind: Kind, budget: Budget | None = None, uses: int = 1
    ) -> bytes:
        target = checked_url(url, kind)
        limit = settings.max_image_bytes if kind == "image" else icon_limit()
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                for hop in range(MAX_REDIRECTS + 1):
                    outcome = await self._hop(target, kind, limit, budget, uses)
                    if isinstance(outcome, bytes):
                        logger.info(
                            "Fetched the %s URL: bytes=%d redirects=%d", kind, len(outcome), hop
                        )
                        return outcome
                    if hop == MAX_REDIRECTS:
                        break
                    target = self._follow(target, outcome, kind)
        except TimeoutError:
            raise _failed(kind, f"no answer within {TIMEOUT_SECONDS} seconds") from None
        except httpx.HTTPError as exc:
            raise _failed(kind, type(exc).__name__) from None
        raise _failed(kind, f"more than {MAX_REDIRECTS} redirects")

    @staticmethod
    def _follow(current: httpx.URL, redirect: Redirect, kind: Kind) -> httpx.URL:
        try:
            joined = current.join(redirect.location)
        except (httpx.InvalidURL, ValueError):
            raise _failed(kind, "a redirect to an invalid location") from None
        return checked_url(joined, kind)

    async def _address(self, host: str, kind: Kind) -> Address:
        literal = _literal(host)
        if literal is not None:
            found = [literal]
        else:
            try:
                found = [ipaddress.ip_address(item) for item in await self._resolve(host)]
            except (OSError, ValueError):
                raise _failed(kind, "the host name did not resolve") from None
        if not found:
            raise _failed(kind, "the host name did not resolve")
        if not all(allowed(address) for address in found):
            raise _refused(kind)
        return found[0]

    def _client(self) -> httpx.AsyncClient:
        # trust_env=False: with a proxy from the environment, the proxy would make the connection.
        return httpx.AsyncClient(
            transport=self._transport,
            verify=self._tls if self._transport is None else True,
            trust_env=False,
            follow_redirects=False,
            timeout=TIMEOUT_SECONDS,
        )

    async def _hop(
        self, url: httpx.URL, kind: Kind, limit: int, budget: Budget | None, uses: int
    ) -> bytes | Redirect:
        host = url.raw_host.decode("ascii")
        address = await self._address(host, kind)
        # A client per hop: a pooled connection to the same address would carry the next host
        # over a TLS session that was verified for the previous one.
        client = self._client()
        try:
            request = client.build_request(
                "GET",
                url.copy_with(host=str(address)),
                headers={
                    "Host": url.netloc.decode("ascii"),
                    "Accept": ACCEPT[kind],
                    "Accept-Encoding": "identity",
                    "User-Agent": USER_AGENT,
                },
                extensions={"sni_hostname": host},
            )
            response = await client.send(request, stream=True)
            try:
                if response.status_code in REDIRECTS:
                    location = response.headers.get("location")
                    if not location:
                        raise _failed(kind, f"status {response.status_code} without a location")
                    return Redirect(location)
                if response.status_code != 200:
                    raise _failed(kind, f"status {response.status_code}")
                return await _body(response, kind, limit, budget, uses)
            finally:
                await response.aclose()
        finally:
            await client.aclose()
