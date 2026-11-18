import base64
import binascii
import hashlib
import hmac
import json
import re
from typing import Any

MAX_TOKEN_LENGTH = 2048
SIGNED = re.compile(r"([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]{43})")


def encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def decode(text: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return None


class Key:
    """An HMAC-SHA256 key of its own for one purpose, derived from MCP_SECRET and the public
    address of the server, so neither another purpose nor another server accepts what it signs."""

    def __init__(self, secret: str, public_url: str, purpose: str):
        context = f"contentgun mcp {purpose} v1 {public_url}".encode()
        self._key = hmac.new(secret.encode("utf-8"), context, hashlib.sha256).digest()

    def digest(self, message: str) -> str:
        return encode(hmac.new(self._key, message.encode("ascii"), hashlib.sha256).digest())

    def sign(self, payload: bytes) -> str:
        body = encode(payload)
        return f"{body}.{self.digest(body)}"

    def verify(self, token: object) -> bytes | None:
        """The payload of a token this key signed; checks the form, then the signature."""
        if not isinstance(token, str) or len(token) > MAX_TOKEN_LENGTH:
            return None
        match = SIGNED.fullmatch(token)
        if match is None:
            return None
        body, signature = match.groups()
        if not hmac.compare_digest(self.digest(body), signature):
            return None
        return decode(body)

    def seal(self, claims: dict[str, Any]) -> str:
        return self.sign(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())

    def open(self, token: object) -> dict[str, Any] | None:
        payload = self.verify(token)
        if payload is None:
            return None
        try:
            claims = json.loads(payload)
        except ValueError:
            return None
        return claims if isinstance(claims, dict) else None
