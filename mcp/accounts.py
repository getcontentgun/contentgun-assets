import logging
import re
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

from errors import ConfigError

logger = logging.getLogger(__name__)

SHORT_FORM_TENANT = "default"
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
# The form app/tenants.py accepts in the tenants file; both must read a file the same way.
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# The stricter form a typed address must have before it goes anywhere near a mail header.
ADDRESS = re.compile(
    r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"
)
MAX_ADDRESS_LENGTH = 254


def normalize(value: str) -> str:
    return value.strip().lower()


def is_address(value: str) -> bool:
    return len(value) <= MAX_ADDRESS_LENGTH and ADDRESS.fullmatch(value) is not None


def mask(address: str) -> str:
    name, _, domain = address.partition("@")
    return f"{name[:1]}…@{domain}"


def read_tenants_file(path: Path) -> dict[str, str]:
    """Every address of the tenants file with its tenant. Messages never name an address."""
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"{path}: TENANTS_FILE names a file that does not exist.") from None
    except OSError as exc:
        raise ConfigError(f"{path}: The tenants file cannot be read: {exc.strerror}.") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: The tenants file is not valid TOML: {exc}.") from None

    table = raw.get("tenants")
    if not isinstance(table, dict) or not table:
        raise ConfigError(
            f"{path}: The tenants file declares no tenant. Add one as [tenants.<name>]."
        )
    owners: dict[str, str] = {}
    for name in sorted(table):
        entry = table[name]
        if not NAME.fullmatch(name):
            raise ConfigError(
                f"{path}: The tenant name {name!r} must use lowercase letters, digits and hyphens."
            )
        if not isinstance(entry, dict):
            raise ConfigError(
                f"{path}: Tenant {name!r} must be a table, written as [tenants.<name>]."
            )
        values = entry.get("emails", [])
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ConfigError(f"{path}: Tenant {name!r} must list its emails as strings.")
        for position, value in enumerate(values, start=1):
            address = normalize(value)
            if not EMAIL.fullmatch(address):
                raise ConfigError(
                    f"{path}: Tenant {name!r} has an entry in emails that is not an email "
                    f"address (entry {position})."
                )
            owner = owners.get(address)
            if owner == name:
                raise ConfigError(
                    f"{path}: Tenant {name!r} lists an email address twice (entry {position})."
                )
            if owner is not None:
                raise ConfigError(
                    f"{path}: Tenant {name!r} shares an email address with tenant {owner!r} "
                    f"(entry {position})."
                )
            owners[address] = name
    return owners


def _stamp(path: Path) -> tuple[int, int, int] | None:
    try:
        status = path.stat()
    except OSError:
        return None
    return status.st_mtime_ns, status.st_size, status.st_ino


def _warn_unreachable(mapping: Mapping[str, str]) -> None:
    for address, tenant in mapping.items():
        if not is_address(address):
            logger.warning(
                "Tenant %s has the address %s, which the sign-in does not accept: it takes "
                "plain ASCII addresses only.",
                tenant,
                mask(address),
            )


class Accounts:
    """Maps an email address to its tenant. A tenants file is read again when it changes; a
    changed file that is invalid or gone leaves the addresses read before in use."""

    def __init__(self, mapping: Mapping[str, str], path: Path | None = None):
        self._mapping = dict(mapping)
        self._path = path
        self._stamp = None if path is None else _stamp(path)

    @classmethod
    def from_file(cls, path: Path) -> "Accounts":
        stamp = _stamp(path)
        accounts = cls(read_tenants_file(path), path)
        accounts._stamp = stamp
        _warn_unreachable(accounts._mapping)
        return accounts

    @classmethod
    def from_addresses(cls, addresses: Iterable[str]) -> "Accounts":
        return cls({address: SHORT_FORM_TENANT for address in addresses})

    def tenant_of(self, address: str) -> str | None:
        self._refresh()
        return self._mapping.get(address)

    def _refresh(self) -> None:
        if self._path is None:
            return
        stamp = _stamp(self._path)
        if stamp == self._stamp:
            return
        self._stamp = stamp
        if stamp is None:
            logger.warning("%s is gone; the addresses read before stay in use.", self._path)
            return
        try:
            mapping = read_tenants_file(self._path)
        except ConfigError as exc:
            logger.warning("The tenants file changed and is invalid, so it was not taken: %s", exc)
            return
        self._mapping = mapping
        _warn_unreachable(mapping)
        logger.info(
            "Read %d addresses of %d tenants from %s.",
            len(mapping),
            len(set(mapping.values())),
            self._path,
        )
