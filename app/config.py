from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    api_keys: str = ""
    debug_routes: bool = False
    strict_fonts: bool = True
    render_concurrency: int = Field(default=2, ge=1, le=16)
    render_timeout_ms: int = Field(default=15000, ge=1000, le=120000)
    queue_timeout_ms: int = Field(default=10000, ge=1000, le=120000)
    max_image_bytes: int = Field(default=8_388_608, ge=65_536, le=33_554_432)
    bundles_dir: Path = ROOT_DIR / "bundles"
    default_template: str = "demo/card"

    @field_validator("bundles_dir")
    @classmethod
    def anchor_bundles_dir(cls, value: Path) -> Path:
        value = value.expanduser()
        return value if value.is_absolute() else ROOT_DIR / value

    @property
    def api_key_list(self) -> list[str]:
        return [key.strip() for key in self.api_keys.split(",") if key.strip()]


settings = Settings()
