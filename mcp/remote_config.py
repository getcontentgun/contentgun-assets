import math
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import AnyUrl, ValidationError

from accounts import is_address, normalize
from errors import ConfigError

MIN_SECRET_LENGTH = 32
MIN_INTERNAL_KEY_LENGTH = 32
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_ACCESS_MINUTES = 60
MAX_ACCESS_MINUTES = 24 * 60
CLAUDE_CALLBACKS = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)
SMTP_PORTS = {"starttls": 587, "tls": 465}
CONSOLE_SENDER = "Contentgun <sign-in@contentgun.invalid>"
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    user: str | None
    password: str | None
    security: str


@dataclass(frozen=True)
class RemoteConfig:
    public_url: str
    secret: str
    base_url: str
    api_key: str
    timeout_s: float
    tenants_file: Path | None
    emails: tuple[str, ...]
    smtp: SmtpSettings | None
    mail_from: str
    sessions_after: int | None
    access_minutes: int
    redirect_uris: tuple[str, ...]
    imprint_url: str | None
    privacy_url: str | None

    @property
    def resource_url(self) -> str:
        return f"{self.public_url}/mcp"

    @property
    def origin(self) -> str:
        parts = urlsplit(self.public_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def is_loopback(self) -> bool:
        return urlsplit(self.public_url).hostname in LOOPBACK_HOSTS


def _value(environ: Mapping[str, str], name: str) -> str:
    return environ.get(name, "").strip()


def _required(environ: Mapping[str, str], name: str) -> str:
    value = _value(environ, name)
    if not value:
        raise ConfigError(f"{name} is not set.")
    return value


def _public_url(value: str) -> str:
    value = value.rstrip("/")
    parts = urlsplit(value)
    loopback = parts.hostname in LOOPBACK_HOSTS
    if (
        not parts.hostname
        or parts.path
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
        or not (parts.scheme == "https" or (parts.scheme == "http" and loopback))
    ):
        raise ConfigError(
            "MCP_PUBLIC_URL must be the https:// address of the server without a path, such as "
            "https://mcp.contentgun.de; http:// only on localhost."
        )
    return f"{parts.scheme}://{parts.netloc.lower()}"


def _http_url(value: str, name: str, example: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigError(f"{name} must be an http:// or https:// URL, for example {example}.")
    return value


def _callback(value: str) -> str:
    try:
        url = AnyUrl(value)
    except ValidationError:
        url = None
    if url is None or url.scheme != "https" or not url.host or url.query or url.fragment:
        raise ConfigError(
            f"MCP_REDIRECT_URIS takes https:// addresses without query or fragment, and "
            f"{value!r} is not one."
        )
    return str(url)


def _timestamp(value: str) -> int:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is None:
        raise ConfigError(
            "MCP_SESSIONS_AFTER must be a date and time with a time zone, such as "
            "2026-11-18T12:00:00Z."
        )
    stamp = int(moment.timestamp())
    if stamp > time.time() + 60:
        raise ConfigError(
            "MCP_SESSIONS_AFTER lies in the future, which would refuse every sign-in until then."
        )
    return stamp


def _smtp(environ: Mapping[str, str]) -> SmtpSettings:
    security = _value(environ, "SMTP_SECURITY").lower() or "starttls"
    if security not in SMTP_PORTS:
        raise ConfigError("SMTP_SECURITY must be starttls or tls.")
    raw_port = _value(environ, "SMTP_PORT")
    if raw_port and not raw_port.isdigit():
        raise ConfigError("SMTP_PORT must be a port number.")
    port = int(raw_port) if raw_port else SMTP_PORTS[security]
    if not 0 < port < 65536:
        raise ConfigError("SMTP_PORT must be a port number.")
    user = _value(environ, "SMTP_USER") or None
    password = environ.get("SMTP_PASSWORD", "") or None
    if (user is None) != (password is None):
        raise ConfigError("SMTP_USER and SMTP_PASSWORD go together: set both or neither.")
    return SmtpSettings(
        host=_required(environ, "SMTP_HOST"),
        port=port,
        user=user,
        password=password,
        security=security,
    )


def _mail_from(value: str) -> str:
    _, address = parseaddr(value)
    if "\r" in value or "\n" in value or not is_address(normalize(address)):
        raise ConfigError(
            "MAIL_FROM must be a sender address, such as 'Contentgun <login@contentgun.de>'."
        )
    return value


def _addresses(value: str) -> tuple[str, ...]:
    addresses = []
    for part in value.split(","):
        address = normalize(part)
        if not address:
            continue
        if not is_address(address):
            raise ConfigError("MCP_EMAILS lists an entry that is not a plain email address.")
        addresses.append(address)
    if not addresses:
        raise ConfigError("MCP_EMAILS lists no address.")
    return tuple(dict.fromkeys(addresses))


def load_remote_config(environ: Mapping[str, str] = os.environ) -> RemoteConfig:
    public_url = _public_url(_required(environ, "MCP_PUBLIC_URL"))

    secret = _required(environ, "MCP_SECRET")
    if len(secret) < MIN_SECRET_LENGTH:
        raise ConfigError(f"MCP_SECRET must be at least {MIN_SECRET_LENGTH} characters long.")

    base_url = _http_url(
        _required(environ, "ASSETS_BASE_URL").rstrip("/"), "ASSETS_BASE_URL", "http://assets:8010"
    )
    api_key = _required(environ, "INTERNAL_KEY")
    if len(api_key) < MIN_INTERNAL_KEY_LENGTH:
        raise ConfigError(
            f"INTERNAL_KEY must be at least {MIN_INTERNAL_KEY_LENGTH} characters long, "
            "like the one of the assets service."
        )
    if api_key == secret:
        raise ConfigError("MCP_SECRET must not be INTERNAL_KEY. Give it a value of its own.")

    raw_timeout = _value(environ, "ASSETS_TIMEOUT_S")
    try:
        timeout_s = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_S
    except ValueError:
        raise ConfigError("ASSETS_TIMEOUT_S must be a number of seconds.") from None
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ConfigError("ASSETS_TIMEOUT_S must be a number of seconds above 0.")

    raw_tenants_file = _value(environ, "TENANTS_FILE")
    raw_emails = _value(environ, "MCP_EMAILS")
    if bool(raw_tenants_file) == bool(raw_emails):
        raise ConfigError(
            "Set exactly one of TENANTS_FILE, the tenants file of the assets service, and "
            "MCP_EMAILS, the addresses of the one tenant 'default'."
        )
    tenants_file = Path(raw_tenants_file).expanduser().resolve() if raw_tenants_file else None
    if tenants_file is not None and not tenants_file.is_file():
        raise ConfigError(f"TENANTS_FILE names {raw_tenants_file!r}, which is no file.")
    emails = _addresses(raw_emails) if raw_emails else ()

    mail = _value(environ, "MCP_MAIL").lower() or "smtp"
    if mail not in ("smtp", "console"):
        raise ConfigError("MCP_MAIL must be smtp or console.")
    if mail == "console":
        if _value(environ, "SMTP_HOST"):
            raise ConfigError(
                "MCP_MAIL=console sends no mail, but SMTP_HOST is set. Leave SMTP_HOST empty "
                "or set MCP_MAIL=smtp."
            )
        smtp = None
        mail_from = _mail_from(_value(environ, "MAIL_FROM") or CONSOLE_SENDER)
    else:
        if not _value(environ, "SMTP_HOST"):
            raise ConfigError(
                "SMTP_HOST is not set. The sign-in sends its codes by mail; for development, "
                "MCP_MAIL=console writes them to the log instead."
            )
        smtp = _smtp(environ)
        mail_from = _mail_from(_required(environ, "MAIL_FROM"))

    raw_sessions_after = _value(environ, "MCP_SESSIONS_AFTER")
    sessions_after = _timestamp(raw_sessions_after) if raw_sessions_after else None

    raw_minutes = _value(environ, "MCP_ACCESS_MINUTES")
    if raw_minutes and not raw_minutes.isdigit():
        raise ConfigError("MCP_ACCESS_MINUTES must be a whole number of minutes.")
    access_minutes = int(raw_minutes) if raw_minutes else DEFAULT_ACCESS_MINUTES
    if not 1 <= access_minutes <= MAX_ACCESS_MINUTES:
        raise ConfigError(
            f"MCP_ACCESS_MINUTES must lie between 1 and {MAX_ACCESS_MINUTES} minutes."
        )

    extra = [
        _callback(part.strip())
        for part in environ.get("MCP_REDIRECT_URIS", "").split(",")
        if part.strip()
    ]
    redirect_uris = tuple(dict.fromkeys([*CLAUDE_CALLBACKS, *extra]))

    links = {}
    for name in ("MCP_IMPRINT_URL", "MCP_PRIVACY_URL"):
        value = _value(environ, name)
        links[name] = _http_url(value, name, "https://contentgun.de/impressum") if value else None

    return RemoteConfig(
        public_url=public_url,
        secret=secret,
        base_url=base_url,
        api_key=api_key,
        timeout_s=timeout_s,
        tenants_file=tenants_file,
        emails=emails,
        smtp=smtp,
        mail_from=mail_from,
        sessions_after=sessions_after,
        access_minutes=access_minutes,
        redirect_uris=redirect_uris,
        imprint_url=links["MCP_IMPRINT_URL"],
        privacy_url=links["MCP_PRIVACY_URL"],
    )
