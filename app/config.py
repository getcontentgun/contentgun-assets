from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[1]
BUILTIN_BUNDLES_DIR = ROOT_DIR / "bundles"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    api_keys: str = ""
    tenants_file: Path | None = None
    internal_key: str = ""
    debug_routes: bool = False
    strict_fonts: bool = True
    render_concurrency: int = Field(default=2, ge=1, le=16)
    render_timeout_ms: int = Field(default=15000, ge=1000, le=120000)
    queue_timeout_ms: int = Field(default=10000, ge=1000, le=120000)
    max_image_bytes: int = Field(default=8_388_608, ge=65_536, le=33_554_432)
    bundles_dir: Path | None = None
    default_template: str = "demo/card"
    s3_endpoint: str = ""
    s3_region: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    public_files_url: str = ""
    storage_ttl_hours: int = Field(default=24, ge=1, le=8760)

    @field_validator("tenants_file", "bundles_dir", mode="before")
    @classmethod
    def blank_means_unset(cls, value: object) -> object:
        # Compose and the test suite blank a path from .env this way; env_ignore_empty would fall
        # back to the line in .env instead.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("tenants_file", "bundles_dir")
    @classmethod
    def anchor_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        value = value.expanduser()
        return value if value.is_absolute() else ROOT_DIR / value

    @property
    def api_key_list(self) -> list[str]:
        return [key.strip() for key in self.api_keys.split(",") if key.strip()]


settings = Settings()
