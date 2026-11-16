import hashlib
import hmac
import re
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.config import BUILTIN_BUNDLES_DIR, settings
from app.templating import (
    BUNDLE_FILE,
    NAME,
    NAME_RULE,
    Bundle,
    TemplateRegistry,
    load_bundles,
    skipped,
)

SHORT_FORM_TENANT = "default"
FILE_KEYS = frozenset({"tenants"})
TENANT_KEYS = frozenset({"api_keys", "emails", "bundles", "default_template"})
KEY_HASH = re.compile(r"sha256:([0-9a-fA-F]{64})")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
MIN_INTERNAL_KEY_LENGTH = 32
KEY_SCRIPT = "scripts/new_api_key.py"


class Internal(Enum):
    """What the internal key authenticates as: a service that names the tenant per request."""

    ACCESS = "internal"


@dataclass(frozen=True, eq=False)
class Tenant:
    name: str
    key_hashes: tuple[bytes, ...]
    emails: tuple[str, ...]
    registry: TemplateRegistry


@dataclass(frozen=True)
class _Entry:
    name: str
    key_hashes: tuple[bytes, ...]
    emails: tuple[str, ...]
    bundles: frozenset[str] | None
    default_template: str | None


def key_digest(key: str) -> bytes:
    return hashlib.sha256(key.encode("utf-8")).digest()


@dataclass(frozen=True)
class TenantSource:
    """What the tenants were loaded from, so that a reload can read it again."""

    builtin_dir: Path
    bundles_dir: Path | None
    tenants_file: Path | None
    api_keys: tuple[str, ...]
    internal_key: str
    default_template: str

    def load(self) -> "TenantDirectory":
        return load_tenants(
            builtin_dir=self.builtin_dir,
            bundles_dir=self.bundles_dir,
            tenants_file=self.tenants_file,
            api_keys=self.api_keys,
            internal_key=self.internal_key,
            default_template=self.default_template,
        )


class TenantDirectory:
    def __init__(
        self,
        tenants: dict[str, Tenant],
        builtin: dict[str, Bundle],
        source: TenantSource,
        internal_digest: bytes | None = None,
    ):
        self._tenants = tenants
        self._builtin = builtin
        self.source = source
        credentials: list[tuple[Tenant | Internal, bytes]] = [
            (tenant, digest) for tenant in tenants.values() for digest in tenant.key_hashes
        ]
        if internal_digest is not None:
            credentials.append((Internal.ACCESS, internal_digest))
        self._credentials = tuple(credentials)
        self.internal_key = internal_digest is not None

    @property
    def names(self) -> list[str]:
        return sorted(self._tenants)

    @property
    def tenants(self) -> list[Tenant]:
        return [self._tenants[name] for name in self.names]

    @property
    def sole(self) -> Tenant | None:
        return next(iter(self._tenants.values())) if len(self._tenants) == 1 else None

    def find(self, name: str) -> Tenant | None:
        return self._tenants.get(name)

    def get(self, name: str) -> Tenant:
        return self._tenants[name]

    @property
    def key_count(self) -> int:
        return sum(len(tenant.key_hashes) for tenant in self._tenants.values())

    @property
    def has_keys(self) -> bool:
        return bool(self._credentials)

    @property
    def builtin_bundles(self) -> list[Bundle]:
        return [self._builtin[name] for name in sorted(self._builtin)]

    @property
    def builtin_ids(self) -> list[str]:
        return sorted(
            spec.id for bundle in self._builtin.values() for spec in bundle.specs.values()
        )

    def authenticate(self, key: str) -> Tenant | Internal | None:
        digest = key_digest(key)
        found: Tenant | Internal | None = None
        # No break on a match: the time taken must not tell which stored key matched.
        for owner, stored in self._credentials:
            if hmac.compare_digest(digest, stored):
                found = owner
        return found


def _strings(value: object, key: str, invalid: Callable[[str], RuntimeError]) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise invalid(f"must list its {key} as strings")
    return value


def _entry(
    path: Path,
    name: str,
    raw: object,
    key_owners: dict[bytes, str],
    email_owners: dict[str, str],
) -> _Entry:
    def invalid(problem: str) -> RuntimeError:
        return RuntimeError(f"{path}: Tenant {name!r} {problem}.")

    if not NAME.fullmatch(name):
        raise RuntimeError(f"{path}: The tenant name {name!r} must use {NAME_RULE}.")
    if not isinstance(raw, dict):
        raise invalid("must be a table, written as [tenants.<name>]")
    unknown = sorted(set(raw) - TENANT_KEYS)
    if unknown:
        raise invalid(f"has the unknown key {unknown[0]!r}")

    hashes = []
    for value in _strings(raw.get("api_keys", []), "api_keys", invalid):
        match = KEY_HASH.fullmatch(value.strip())
        if match is None:
            # The value is left out of the message: it may be a key pasted in by mistake.
            raise invalid(
                "has an entry in api_keys that is no hash of the form 'sha256:<64 hex digits>'. "
                f"Create a key and its hash with {KEY_SCRIPT}"
            )
        digest = bytes.fromhex(match.group(1))
        owner = key_owners.get(digest)
        if owner == name:
            raise invalid("lists the same API key twice")
        if owner is not None:
            raise invalid(f"shares an API key with tenant {owner!r}")
        key_owners[digest] = name
        hashes.append(digest)

    emails = []
    for value in _strings(raw.get("emails", []), "emails", invalid):
        address = value.strip().lower()
        if not EMAIL.fullmatch(address):
            raise invalid(f"has the email address {value!r}, which is not one")
        owner = email_owners.get(address)
        if owner == name:
            raise invalid(f"lists the email address {value!r} twice")
        if owner is not None:
            raise invalid(f"shares the email address {value!r} with tenant {owner!r}")
        email_owners[address] = name
        emails.append(address)

    bundles = None
    if "bundles" in raw:
        listed = _strings(raw["bundles"], "bundles", invalid)
        if not listed:
            raise invalid("lists no bundle in bundles. Leave the key out to give it every bundle")
        bundles = frozenset(listed)

    default = raw.get("default_template")
    if default is not None and (not isinstance(default, str) or not default.strip()):
        raise invalid("has a default_template that is not a template id")
    return _Entry(name, tuple(hashes), tuple(emails), bundles, default)


def _read_tenants_file(path: Path) -> list[_Entry]:
    if not path.is_file():
        raise RuntimeError(f"{path}: TENANTS_FILE names a file that does not exist.")
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(f"{path}: The tenants file is not valid TOML: {exc}.") from None
    unknown = sorted(set(raw) - FILE_KEYS)
    if unknown:
        raise RuntimeError(f"{path}: The tenants file has the unknown key {unknown[0]!r}.")
    table = raw.get("tenants")
    if not isinstance(table, dict) or not table:
        raise RuntimeError(
            f"{path}: The tenants file declares no tenant. Add one as [tenants.<name>]."
        )
    key_owners: dict[bytes, str] = {}
    email_owners: dict[str, str] = {}
    return [_entry(path, name, table[name], key_owners, email_owners) for name in sorted(table)]


def _short_form(api_keys: Sequence[str]) -> _Entry:
    digests = tuple(dict.fromkeys(key_digest(key) for key in api_keys))
    return _Entry(SHORT_FORM_TENANT, digests, (), None, None)


def _tenant_folders(
    bundles_dir: Path | None,
    builtin_dir: Path,
    tenants: set[str],
    tenants_file: Path | None,
) -> dict[str, Path]:
    if bundles_dir is None:
        return {}
    if not bundles_dir.is_dir():
        raise RuntimeError(f"{bundles_dir}: BUNDLES_DIR names a folder that does not exist.")
    if builtin_dir.is_dir() and bundles_dir.resolve() == builtin_dir.resolve():
        raise RuntimeError(
            f"{bundles_dir}: BUNDLES_DIR names the folder of the built-in bundles, which load on "
            "their own. Leave it empty, or name a folder with one subfolder per tenant."
        )
    folders = {}
    for path in sorted(p for p in bundles_dir.iterdir() if p.is_dir() and not skipped(p)):
        if (path / BUNDLE_FILE).is_file():
            target = (
                f"{bundles_dir / SHORT_FORM_TENANT}/"
                if tenants_file is None
                else "the folder of its tenant"
            )
            raise RuntimeError(
                f"{path}: This folder is a bundle, but BUNDLES_DIR holds one folder per tenant, "
                f"with the bundles of that tenant inside. Move it into {target}."
            )
        if path.name not in tenants:
            hint = (
                f"With API_KEYS the only tenant is {SHORT_FORM_TENANT!r}; name further tenants "
                "in a tenants file"
                if tenants_file is None
                else f"Add the tenant to {tenants_file}, or move the folder out of BUNDLES_DIR"
            )
            raise RuntimeError(f"{path}: This folder belongs to no tenant. {hint}.")
        folders[path.name] = path
    return folders


def _registry(
    entry: _Entry,
    own: dict[str, Bundle],
    builtin: dict[str, Bundle],
    folder: Path | None,
    default_template: str,
    tenants_file: Path | None,
) -> TemplateRegistry:
    for name in own:
        if name in builtin:
            raise RuntimeError(
                f"{folder / name}: Bundle {name!r} of tenant {entry.name!r} has the name of a "
                "built-in bundle. Give it another name."
            )
    available = builtin | own
    if entry.bundles is not None:
        unknown = sorted(entry.bundles - set(available))
        if unknown:
            raise RuntimeError(
                f"{tenants_file}: Tenant {entry.name!r} lists the bundle {unknown[0]!r}, which "
                "is neither one of its own bundles nor a built-in one."
            )
        available = {name: bundle for name, bundle in available.items() if name in entry.bundles}

    default = entry.default_template or default_template
    registry = TemplateRegistry(available, default)
    if default not in registry.ids:
        offered = ", ".join(registry.ids) or "none"
        if tenants_file is None:
            message = f"DEFAULT_TEMPLATE {default!r} is not a known template"
        elif entry.default_template is None:
            message = (
                f"{tenants_file}: Tenant {entry.name!r} cannot use DEFAULT_TEMPLATE {default!r}. "
                "Give it a default_template of its own"
            )
        else:
            message = (
                f"{tenants_file}: Tenant {entry.name!r} has the default_template {default!r}, "
                "which is not one of its templates"
            )
        raise RuntimeError(f"{message}. Available: {offered}.")
    return registry


def load_tenants(
    *,
    builtin_dir: Path,
    bundles_dir: Path | None,
    tenants_file: Path | None,
    api_keys: Sequence[str],
    internal_key: str,
    default_template: str,
) -> TenantDirectory:
    """Every tenant with the keys it authenticates with and the templates it may use: the
    built-in bundles and the bundles of its own folder in bundles_dir, validated once, at
    startup. Without a tenants file, api_keys make up the one tenant 'default'."""
    source = TenantSource(
        builtin_dir, bundles_dir, tenants_file, tuple(api_keys), internal_key, default_template
    )
    builtin = load_bundles(builtin_dir) if builtin_dir.is_dir() else {}

    if tenants_file is None:
        entries = [_short_form(api_keys)]
    elif api_keys:
        raise RuntimeError(
            "API_KEYS and TENANTS_FILE are both set. Give the keys to the tenants in the tenants "
            "file, or leave TENANTS_FILE empty."
        )
    else:
        entries = _read_tenants_file(tenants_file)

    folders = _tenant_folders(
        bundles_dir, builtin_dir, {entry.name for entry in entries}, tenants_file
    )
    tenants = {}
    for entry in entries:
        folder = folders.get(entry.name)
        own = load_bundles(folder) if folder is not None else {}
        registry = _registry(entry, own, builtin, folder, default_template, tenants_file)
        tenants[entry.name] = Tenant(entry.name, entry.key_hashes, entry.emails, registry)

    internal_digest = None
    internal_key = internal_key.strip()
    if internal_key:
        if len(internal_key) < MIN_INTERNAL_KEY_LENGTH:
            raise RuntimeError(
                f"INTERNAL_KEY must be at least {MIN_INTERNAL_KEY_LENGTH} characters long."
            )
        internal_digest = key_digest(internal_key)
        for tenant in tenants.values():
            if internal_digest in tenant.key_hashes:
                raise RuntimeError(
                    f"INTERNAL_KEY is also an API key of tenant {tenant.name!r}. Give the internal "
                    "key a value of its own."
                )
    return TenantDirectory(tenants, builtin, source, internal_digest)


def configured_source() -> TenantSource:
    return TenantSource(
        builtin_dir=BUILTIN_BUNDLES_DIR,
        bundles_dir=settings.bundles_dir,
        tenants_file=settings.tenants_file,
        api_keys=tuple(settings.api_key_list),
        internal_key=settings.internal_key,
        default_template=settings.default_template,
    )


def load_configured_tenants() -> TenantDirectory:
    return configured_source().load()
