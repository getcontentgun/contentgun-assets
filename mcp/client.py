import logging
from dataclasses import dataclass
from typing import Any

import httpx

from config import Config
from errors import AssetsError

logger = logging.getLogger(__name__)

HINTS = {
    400: "Retrying the same request fails again.",
    413: "Use smaller images or icons; a PDF counts those of all its slides together.",
    503: "This is temporary, retrying is safe.",
    504: "Retrying is safe.",
}


class AssetsApiError(AssetsError):
    pass


@dataclass(frozen=True)
class Rendered:
    data: bytes
    request_id: str


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


class AssetsClient:
    def __init__(self, config: Config, transport: httpx.AsyncBaseTransport | None = None):
        self._config = config
        self._transport = transport

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

    def _file(self, response: httpx.Response, media_type: str) -> Rendered:
        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        if response.status_code != 200 or content_type != media_type:
            raise self._failure(response)
        return Rendered(data=response.content, request_id=response.headers.get("x-request-id", ""))

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        base_url = self._config.base_url
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                timeout=self._config.timeout_s,
                transport=self._transport,
            ) as client:
                return await client.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise AssetsApiError(
                f"{base_url} did not answer within {self._config.timeout_s:g} seconds."
            ) from None
        except httpx.TransportError:
            raise AssetsApiError(
                f"Could not reach {base_url}. Locally the service runs with bin/development."
            ) from None

    def _failure(self, response: httpx.Response) -> AssetsApiError:
        base_url = self._config.base_url
        status = response.status_code
        if status == 401:
            return AssetsApiError(f"The service at {base_url} rejected ASSETS_API_KEY.")
        body = _json_object(response)
        message = body.get("error") if body and body.get("ok") is False else None
        if status == 200 or not isinstance(message, str):
            return AssetsApiError(f"Unexpected response from {base_url}: HTTP {status}.")
        hint = HINTS.get(status)
        return AssetsApiError(f"{message} {hint}" if hint else message)
