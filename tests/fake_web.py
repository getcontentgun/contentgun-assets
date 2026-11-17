import socket
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx

from app.fetching import RemoteImages

# Public addresses as the checks see them; the tests never connect to them.
PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"
CDN_V4 = "151.101.1.69"

Page = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


def response(
    data: bytes, status: int = 200, headers: dict[str, str] | None = None
) -> httpx.Response:
    """A response whose body is still to be read, as from a real connection: built from content,
    httpx reads it at once, and the raw stream the service reads is then gone."""
    declared = {"Content-Length": str(len(data))} | (headers or {})
    return httpx.Response(status, headers=declared, stream=httpx.ByteStream(data))


def serve(data: bytes, status: int = 200, headers: dict[str, str] | None = None) -> Page:
    return lambda request: response(data, status, headers)


def streamed(
    chunks: Callable[[], AsyncIterator[bytes]], headers: dict[str, str] | None = None
) -> Page:
    """A body that arrives chunk by chunk, without Content-Length unless headers declare one."""
    return lambda request: httpx.Response(200, headers=headers, content=chunks())


def redirect(location: str, status: int = 302) -> Page:
    return lambda request: httpx.Response(status, headers={"Location": location})


class FakeWeb:
    """Host names, their addresses and the pages behind them, for RemoteImages without a network.
    A host answers each lookup with the next of its answers and repeats the last one; a page is
    found by the Host header and the path of the request. Both record what they are asked."""

    def __init__(self) -> None:
        self.hosts: dict[str, list[list[str]]] = {}
        self.pages: dict[str, Page] = {}
        self.lookups: list[str] = []
        self.requests: list[httpx.Request] = []

    def reset(self) -> None:
        self.hosts.clear()
        self.pages.clear()
        self.lookups.clear()
        self.requests.clear()

    def add_host(self, host: str, *addresses: str) -> None:
        self.hosts[host] = [list(addresses)]

    def answer(self, host: str, *answers: list[str]) -> None:
        self.hosts[host] = [list(answer) for answer in answers]

    async def resolve(self, host: str) -> list[str]:
        self.lookups.append(host)
        answers = self.hosts.get(host)
        if answers is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return answers.pop(0) if len(answers) > 1 else answers[0]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        page = self.pages.get(f"https://{request.headers['host']}{request.url.path}")
        if page is None:
            return httpx.Response(404)
        response = page(request)
        return response if isinstance(response, httpx.Response) else await response

    def remote(self) -> RemoteImages:
        return RemoteImages(resolve=self.resolve, transport=httpx.MockTransport(self.handle))
