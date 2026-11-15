import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://api.contentgun.de"
DEFAULT_TIMEOUT_S = 30.0
BUNDLE_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    timeout_s: float
    output_root: Path
    image_roots: tuple[Path, ...]
    bundle: str | None = None


def _directory(value: str, name: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConfigError(f"{name} must be an absolute path, not {value!r}.")
    if not path.is_dir():
        raise ConfigError(f"{name} must be an existing directory, and {value!r} is not one.")
    return path.resolve()


def load_config(environ: Mapping[str, str] = os.environ) -> Config:
    api_key = environ.get("ASSETS_API_KEY", "").strip()
    if not api_key:
        raise ConfigError("ASSETS_API_KEY is not set.")

    base_url = environ.get("ASSETS_BASE_URL", "").strip().rstrip("/") or DEFAULT_BASE_URL
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigError(
            "ASSETS_BASE_URL must be an http:// or https:// URL, for example http://127.0.0.1:8010."
        )

    raw_timeout = environ.get("ASSETS_TIMEOUT_S", "").strip()
    try:
        timeout_s = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_S
    except ValueError:
        raise ConfigError("ASSETS_TIMEOUT_S must be a number of seconds.") from None
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ConfigError("ASSETS_TIMEOUT_S must be a number of seconds above 0.")

    raw_output_root = environ.get("ASSETS_OUTPUT_ROOT", "").strip()
    if not raw_output_root:
        raise ConfigError("ASSETS_OUTPUT_ROOT is not set.")
    output_root = _directory(raw_output_root, "ASSETS_OUTPUT_ROOT")

    raw_image_roots = environ.get("ASSETS_IMAGE_ROOTS", "").split(":")
    image_roots = tuple(
        _directory(part.strip(), "ASSETS_IMAGE_ROOTS") for part in raw_image_roots if part.strip()
    )

    bundle = environ.get("ASSETS_BUNDLE", "").strip() or None
    if bundle is not None and not BUNDLE_NAME.fullmatch(bundle):
        raise ConfigError(
            f"ASSETS_BUNDLE must be the name of a bundle, such as 'demo', not {bundle!r}."
        )

    return Config(
        api_key=api_key,
        base_url=base_url,
        timeout_s=timeout_s,
        output_root=output_root,
        image_roots=image_roots or (output_root,),
        bundle=bundle,
    )
