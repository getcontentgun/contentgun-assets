from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.formats import Format

MAX_FIELD_LENGTH = 200


class RenderRequest(BaseModel):
    # Extra keys are allowed so a new template's fields work without a schema change;
    # they are validated against the template's declared fields in the router.
    model_config = ConfigDict(extra="allow")

    template: str | None = None
    format: Format | None = None

    headline: str = Field(min_length=1, max_length=140)
    subline: str | None = Field(default=None, max_length=MAX_FIELD_LENGTH)
    handle: str | None = Field(default=None, max_length=60)
    # The cover's kicker is not fitted, so above this a long run of wide capitals squeezes the
    # headline below its floor at 1:1.
    topic: str | None = Field(default=None, max_length=60)
    # Never wrapped, so this cap is what keeps it on the handle's row inside the card.
    pagination: str | None = Field(default=None, max_length=16)
    # Deliberately uncapped here: the size is checked in bytes by app/images.py, and the
    # field length limit that applies to template fields would reject any real image.
    image: str | None = None
    icon: str | None = None
    icon_color: Literal["accent", "text"] | None = None


RESERVED_KEYS = frozenset({"template", "format", "image", "icon"})

MAX_DOCUMENT_SLIDES = 20


class DocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slides: list[RenderRequest] = Field(min_length=1, max_length=MAX_DOCUMENT_SLIDES)


class FormatInfo(BaseModel):
    format: Format
    width: int
    height: int


class TemplateInfo(BaseModel):
    id: str
    bundle: str
    name: str
    description: str
    formats: list[FormatInfo]
    fields: list[str]
    required: list[str]
    defaults: dict[str, str]
    limits: dict[str, int] = Field(default_factory=dict)
    accepts_image: bool = False
    accepts_icon: bool = False


class BundleInfo(BaseModel):
    name: str
    description: str


class TemplatesResponse(BaseModel):
    ok: bool = True
    bundles: list[BundleInfo]
    templates: list[TemplateInfo]


class HealthResponse(BaseModel):
    ok: bool = True
    version: str
    browser: str
    templates: list[str]
