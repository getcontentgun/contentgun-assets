import hashlib
import hmac
import logging
import re
import secrets
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from accounts import Accounts, mask
from errors import ConfigError
from login import Login, Logins, LoginsFull
from remote_config import RemoteConfig
from signing import Key, encode

logger = logging.getLogger(__name__)

SCOPES = ("cards", "offline_access")
REQUIRED_SCOPE = "cards"
SECRET_METHODS = ("client_secret_post", "client_secret_basic")
GRANT_TYPES = ("authorization_code", "refresh_token")
MAX_REDIRECT_URIS = 4
CLIENT_VERSION = 1
CLIENT_HEADER = struct.Struct(">BBI")
NONCE_BYTES = 16
URI_DIGEST_BYTES = 4
BASIC_FLAG = 1
CODE_SECONDS = 120
MAX_CODES = 1000
REFRESH_SECONDS = 30 * 24 * 3600
SESSION_SECONDS = 90 * 24 * 3600
MAX_STATE_LENGTH = 2048
CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")


def granted(scopes: list[str] | None) -> list[str]:
    """What a token carries: what was asked for, and always cards, without which every tool
    call would fail."""
    asked = set(scopes or SCOPES) | {REQUIRED_SCOPE}
    return [scope for scope in SCOPES if scope in asked]


def binding(client_id: str) -> str:
    return encode(hashlib.sha256(client_id.encode()).digest()[:16])


def uri_digest(uri: str) -> bytes:
    return hashlib.sha256(uri.encode()).digest()[:URI_DIGEST_BYTES]


class Clients:
    """Clients without storage: the client id is signed and names the redirect URIs by digest,
    and the client secret is derived from the id."""

    def __init__(self, config: RemoteConfig, clock: Callable[[], float]):
        self._ids = Key(config.secret, config.public_url, "client id")
        self._secrets = Key(config.secret, config.public_url, "client secret")
        self._allowed = {uri_digest(uri): uri for uri in config.redirect_uris}
        if len(self._allowed) != len(config.redirect_uris):
            raise ConfigError("Two redirect URIs share a digest; rename one of MCP_REDIRECT_URIS.")
        self._clock = clock

    def register(self, info: OAuthClientInformationFull) -> None:
        """Turns what the client asked for into the registered client. The SDK answers the
        registration with this very object, so the client learns its id and secret from it."""
        method = info.token_endpoint_auth_method or SECRET_METHODS[0]
        if method not in SECRET_METHODS:
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description=(
                    "This server registers clients with a secret only: "
                    "token_endpoint_auth_method must be client_secret_post or client_secret_basic."
                ),
            )
        uris = list(dict.fromkeys(str(uri) for uri in info.redirect_uris or []))
        if not 1 <= len(uris) <= MAX_REDIRECT_URIS:
            raise RegistrationError(
                error="invalid_redirect_uri",
                error_description=f"Register between 1 and {MAX_REDIRECT_URIS} redirect URIs.",
            )
        allowed = set(self._allowed.values())
        if any(uri not in allowed for uri in uris):
            raise RegistrationError(
                error="invalid_redirect_uri",
                error_description="This server signs in Claude and takes its callback only.",
            )
        issued = int(self._clock())
        flags = BASIC_FLAG if method == "client_secret_basic" else 0
        payload = (
            CLIENT_HEADER.pack(CLIENT_VERSION, flags, issued)
            + secrets.token_bytes(NONCE_BYTES)
            + b"".join(uri_digest(uri) for uri in uris)
        )
        client_id = self._ids.sign(payload)
        info.client_id = client_id
        info.client_secret = self._secrets.digest(client_id)
        info.client_id_issued_at = issued
        info.client_secret_expires_at = 0
        info.token_endpoint_auth_method = method
        info.redirect_uris = [AnyUrl(uri) for uri in uris]
        info.grant_types = list(GRANT_TYPES)
        info.response_types = ["code"]
        info.scope = " ".join(SCOPES)

    def find(self, client_id: str) -> OAuthClientInformationFull | None:
        payload = self._ids.verify(client_id)
        if payload is None:
            return None
        start = CLIENT_HEADER.size + NONCE_BYTES
        size = len(payload) - start
        if size <= 0 or size % URI_DIGEST_BYTES or size // URI_DIGEST_BYTES > MAX_REDIRECT_URIS:
            return None
        version, flags, issued = CLIENT_HEADER.unpack_from(payload)
        if version != CLIENT_VERSION or flags & ~BASIC_FLAG:
            return None
        digests = [
            payload[offset : offset + URI_DIGEST_BYTES]
            for offset in range(start, len(payload), URI_DIGEST_BYTES)
        ]
        uris = [self._allowed[digest] for digest in digests if digest in self._allowed]
        if not uris:
            return None
        return OAuthClientInformationFull(
            client_id=client_id,
            client_secret=self._secrets.digest(client_id),
            client_id_issued_at=issued,
            client_secret_expires_at=0,
            redirect_uris=uris,
            token_endpoint_auth_method=SECRET_METHODS[1 if flags & BASIC_FLAG else 0],
            grant_types=list(GRANT_TYPES),
            response_types=["code"],
            scope=" ".join(SCOPES),
        )


@dataclass(frozen=True)
class Grant:
    """What an access or a refresh token says: who signed in, for which tenant and client,
    with which scopes, since when and until when."""

    address: str
    tenant: str
    binding: str
    scopes: list[str]
    signed_in: int
    expires: int

    @classmethod
    def read(cls, claims: dict[str, Any] | None) -> "Grant | None":
        if claims is None:
            return None
        address, tenant, bound, scopes = (claims.get(key) for key in ("e", "t", "c", "s"))
        signed_in, expires = claims.get("l"), claims.get("x")
        if not all(isinstance(value, str) for value in (address, tenant, bound, scopes)):
            return None
        if type(signed_in) is not int or type(expires) is not int:
            return None
        return cls(address, tenant, bound, scopes.split(), signed_in, expires)


class Provider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(
        self,
        config: RemoteConfig,
        accounts: Accounts,
        logins: Logins,
        clock: Callable[[], float],
    ):
        self._config = config
        self._accounts = accounts
        self._logins = logins
        self._clock = clock
        self._clients = Clients(config, clock)
        self._access = Key(config.secret, config.public_url, "access token")
        self._refresh = Key(config.secret, config.public_url, "refresh token")
        self._codes: dict[str, AuthorizationCode] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.find(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients.register(client_info)
        hosts = sorted({urlsplit(str(uri)).hostname or "" for uri in client_info.redirect_uris})
        logger.info("Registered a client for %s.", ", ".join(hosts))

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if params.resource is not None and params.resource.rstrip("/") != self._config.resource_url:
            raise AuthorizeError(
                error="invalid_target",
                error_description="The resource must be the MCP endpoint of this server.",
            )
        if params.state is not None and len(params.state) > MAX_STATE_LENGTH:
            raise AuthorizeError(
                error="invalid_request", error_description="The state is too long."
            )
        if not CHALLENGE.fullmatch(params.code_challenge):
            raise AuthorizeError(
                error="invalid_request",
                error_description="The code challenge must be the S256 hash of a PKCE verifier.",
            )
        try:
            login = self._logins.start(client.client_id, params)
        except LoginsFull:
            raise AuthorizeError(
                error="temporarily_unavailable",
                error_description="Too many sign-ins are in progress. Try again in a few minutes.",
            ) from None
        return f"{self._config.public_url}/login?{urlencode({'txn': login.txn})}"

    def finish(self, login: Login) -> str:
        """The redirect back to the client once the right code was typed."""
        now = self._clock()
        for code in [code for code, found in self._codes.items() if found.expires_at <= now]:
            del self._codes[code]
        if len(self._codes) >= MAX_CODES:
            del self._codes[next(iter(self._codes))]
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=granted(login.params.scopes),
            expires_at=now + CODE_SECONDS,
            client_id=login.client_id,
            code_challenge=login.params.code_challenge,
            redirect_uri=login.params.redirect_uri,
            redirect_uri_provided_explicitly=login.params.redirect_uri_provided_explicitly,
            resource=self._config.resource_url,
            subject=login.address,
        )
        return construct_redirect_uri(
            str(login.params.redirect_uri), code=code, state=login.params.state
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        found = self._codes.get(authorization_code)
        if found is None:
            return None
        if found.expires_at <= self._clock():
            del self._codes[authorization_code]
            return None
        return found

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if self._codes.pop(authorization_code.code, None) is None:
            raise TokenError(
                error="invalid_grant", error_description="The authorization code was used already."
            )
        address = authorization_code.subject or ""
        tenant = self._accounts.tenant_of(address)
        if tenant is None:
            raise TokenError(
                error="invalid_grant", error_description="The address is not enabled any more."
            )
        logger.info("Signed in %s of tenant %s.", mask(address), tenant)
        return self._issue(
            client.client_id, address, tenant, authorization_code.scopes, int(self._clock())
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        grant = Grant.read(self._refresh.open(refresh_token))
        if grant is None or not hmac.compare_digest(grant.binding, binding(client.client_id)):
            return None
        refusal = self._refusal(grant)
        if refusal is not None:
            logger.info("Refused to renew the sign-in of %s: %s.", mask(grant.address), refusal)
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=grant.scopes,
            expires_at=grant.expires,
            resource=self._config.resource_url,
            subject=grant.address,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        grant = Grant.read(self._refresh.open(refresh_token.token))
        if grant is None or self._refusal(grant) is not None:
            raise TokenError(
                error="invalid_grant", error_description="The refresh token is no longer valid."
            )
        return self._issue(client.client_id, grant.address, grant.tenant, scopes, grant.signed_in)

    async def load_access_token(self, token: str) -> AccessToken | None:
        grant = Grant.read(self._access.open(token))
        if grant is None or self._refusal(grant) is not None:
            return None
        return AccessToken(
            token=token,
            client_id=grant.binding,
            scopes=grant.scopes,
            expires_at=grant.expires,
            resource=self._config.resource_url,
            subject=grant.address,
            claims={"tenant": grant.tenant, "signed_in_at": grant.signed_in},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        return None

    def _refusal(self, grant: Grant) -> str | None:
        now = self._clock()
        if grant.expires <= now:
            return "the token expired"
        if grant.signed_in + SESSION_SECONDS <= now:
            return "the sign-in is older than 90 days"
        after = self._config.sessions_after
        if after is not None and grant.signed_in < after:
            return "the sign-in predates MCP_SESSIONS_AFTER"
        if REQUIRED_SCOPE not in grant.scopes:
            return "the token lacks the scope cards"
        tenant = self._accounts.tenant_of(grant.address)
        if tenant is None:
            return "the address belongs to no tenant any more"
        if tenant != grant.tenant:
            return f"the address moved to tenant {tenant}"
        return None

    def _issue(
        self, client_id: str, address: str, tenant: str, scopes: list[str], signed_in: int
    ) -> OAuthToken:
        now = int(self._clock())
        session_end = signed_in + SESSION_SECONDS
        scopes = granted(scopes)
        claims = {
            "e": address,
            "t": tenant,
            "c": binding(client_id),
            "s": " ".join(scopes),
            "l": signed_in,
        }
        access_expires = min(now + self._config.access_minutes * 60, session_end)
        refresh_expires = min(now + REFRESH_SECONDS, session_end)
        return OAuthToken(
            access_token=self._access.seal(claims | {"x": access_expires}),
            token_type="Bearer",
            expires_in=access_expires - now,
            scope=" ".join(scopes),
            refresh_token=self._refresh.seal(claims | {"x": refresh_expires}),
        )
