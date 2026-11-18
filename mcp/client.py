import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from errors import AssetsError

logger = logging.getLogger(__name__)
# httpx logs every request URL on INFO, and the path of a stored file is its only protection.
logging.getLogger("httpx").setLevel(logging.WARNING)

HINTS = {
    400: "Retrying the same request fails again.",
    413: "Use smaller images or icons; a PDF counts those of all its slides together.",
    503: "This is temporary, retrying is safe.",
    504: "Retrying is safe.",
}
TENANT_HEADER = "X-Contentgun-Tenant"
STORED_PATH = "/o/"


class AssetsApiError(AssetsError):
    pass


class Connection(Protocol):
    base_url: str
    api_key: str
    timeout_s: float


@dataclass(frozen=True)
class Wording:
    """The sentences for failures that are not the service's own messages. {service} is the
    base URL, {seconds} the timeout and {status} the HTTP status."""

    timeout: str
    unreachable: str
    rejected: str
    unexpected: str


LOCAL = Wording(
    timeout="{service} did not answer within {seconds:g} seconds.",
    unreachable="Could not reach {service}. Locally the service runs with bin/development.",
    rejected="The service at {service} rejected ASSETS_API_KEY.",
    unexpected="Unexpected response from {service}: HTTP {status}.",
)


@dataclass(frozen=True)
class Rendered:
    data: bytes
    request_id: str


@dataclass(frozen=True)
class Stored:
    url: str
    content_type: str
    width: int
    height: int
    size: int
    expires_at: str
    request_id: str
    pages: int | None = None


@dataclass(frozen=True)
class Upload:
    upload_id: str
    upload_url: str
    expires_at: str
    max_bytes: int


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _typed(body: Mapping[str, Any], fields: Mapping[str, type]) -> bool:
    return all(
        isinstance(body.get(name), kind) and not isinstance(body.get(name), bool)
        for name, kind in fields.items()
    )


class AssetsClient:
    def __init__(
        self,
        config: Connection,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        wording: Wording = LOCAL,
        headers: Mapping[str, str] | None = None,
    ):
        self._config = config
        self._transport = transport
        self._wording = wording
        self._headers = dict(headers or {})

    def for_tenant(self, tenant: str) -> "AssetsClient":
        """The same client, acting for one tenant with the internal key."""
        return AssetsClient(
            self._config,
            self._transport,
            wording=self._wording,
            headers=self._headers | {TENANT_HEADER: tenant},
        )

    async def list_templates(self) -> list[dict[str, Any]]:
        response = await self._send("GET", "/v1/templates")
        body = _json_object(response) if response.status_code == 200 else None
        templates = body.get("templates") if body else None
        if not isinstance(templates, list):
            raise self._failure(response)
        logger.info("Listed %d templates from %s", len(templates), self._config.base_url)
        return templates

    async def render(self, body: dict[str, Any]) -> Rendered:
        response = await self._send("POST", "/v1/render", json=body)
        return self._file(response, "image/png")

    async def document(self, slides: list[dict[str, Any]]) -> Rendered:
        response = await self._send("POST", "/v1/document", json={"slides": slides})
        return self._file(response, "application/pdf")

    async def render_stored(self, body: dict[str, Any]) -> Stored:
        response = await self._send("POST", "/v1/render", json=body | {"output": "url"})
        return self._stored(response)

    async def document_stored(self, slides: list[dict[str, Any]]) -> Stored:
        response = await self._send(
            "POST", "/v1/document", json={"slides": slides, "output": "url"}
        )
        stored = self._stored(response)
        if stored.pages is None:
            raise self._failure(response, unexpected=True)
        return stored

    async def create_upload(self) -> Upload:
        response = await self._send("POST", "/v1/uploads")
        body = _json_object(response) if response.status_code == 200 else None
        fields = {"upload_id": str, "upload_url": str, "expires_at": str, "max_bytes": int}
        if body is None or not _typed(body, fields):
            raise self._failure(response)
        return Upload(**{name: body[name] for name in fields})

    async def stored_file(self, url: str) -> bytes:
        """A stored result, fetched from the service itself rather than through the public
        address the URL names. It needs no key, so none is sent."""
        path = urlsplit(url).path
        if not path.startswith(STORED_PATH):
            raise AssetsApiError("The service answered with a URL that is no stored result.")
        response = await self._send("GET", path, authorized=False)
        if response.status_code != 200:
            raise self._failure(response)
        return response.content

    def _file(self, response: httpx.Response, media_type: str) -> Rendered:
        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        if response.status_code != 200 or content_type != media_type:
            raise self._failure(response)
        return Rendered(data=response.content, request_id=response.headers.get("x-request-id", ""))

    def _stored(self, response: httpx.Response) -> Stored:
        body = _json_object(response) if response.status_code == 200 else None
        fields = {
            "url": str,
            "content_type": str,
            "width": int,
            "height": int,
            "bytes": int,
            "expires_at": str,
        }
        pages = None if body is None else body.get("pages")
        if body is None or not _typed(body, fields) or not isinstance(pages, int | None):
            raise self._failure(response)
        return Stored(
            url=body["url"],
            content_type=body["content_type"],
            width=body["width"],
            height=body["height"],
            size=body["bytes"],
            expires_at=body["expires_at"],
            request_id=response.headers.get("x-request-id", ""),
            pages=pages,
        )

    async def _send(
        self, method: str, path: str, *, authorized: bool = True, **kwargs: Any
    ) -> httpx.Response:
        base_url = self._config.base_url
        headers = dict(self._headers) if authorized else {}
        if authorized:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                headers=headers,
                timeout=self._config.timeout_s,
                transport=self._transport,
            ) as client:
                return await client.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise AssetsApiError(
                self._wording.timeout.format(service=base_url, seconds=self._config.timeout_s)
            ) from None
        except httpx.TransportError:
            raise AssetsApiError(self._wording.unreachable.format(service=base_url)) from None

    def _failure(self, response: httpx.Response, *, unexpected: bool = False) -> AssetsApiError:
        base_url = self._config.base_url
        status = response.status_code
        if status == 401:
            return AssetsApiError(self._wording.rejected.format(service=base_url))
        body = _json_object(response)
        message = body.get("error") if body and body.get("ok") is False else None
        if unexpected or status == 200 or not isinstance(message, str):
            return AssetsApiError(self._wording.unexpected.format(service=base_url, status=status))
        hint = HINTS.get(status)
        return AssetsApiError(f"{message} {hint}" if hint else message)
