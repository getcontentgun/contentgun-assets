from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from errors import AssetsError

MAX_CAROUSEL_SLIDES = 20
CAROUSEL_COVER = "carousel-cover"
CAROUSEL_CONTENT = "carousel-content"
CAROUSEL_CLOSING = "carousel-closing"
CAROUSEL_TEMPLATES = (CAROUSEL_COVER, CAROUSEL_CONTENT, CAROUSEL_CLOSING)
DEFAULT_PAGINATION = "{page}/{pages}"

LIST_TEMPLATES = """\
List the templates the contentgun-assets service renders: for every template its id \
(bundle/template), its bundle, description, formats with pixel sizes, the fields it accepts, \
which are required, their defaults, and whether it takes an image or an icon. Call it whenever \
you are unsure which template or fields to use, instead of guessing."""

LIMITS = """\
Limits the service enforces: headline 1 to 140 characters; subline, body and cta up to 200; \
handle and topic up to 60; pagination up to 16. A template may set tighter limits of its own, \
which list_templates reports as limits. A literal newline forces a line break. No emoji."""

CAROUSEL_WRITING = """\
Write it like this: the cover's headline is the hook, about six words, its topic a short kicker \
above it, and its subline one line under the rule that sets up the carousel. Each content slide \
makes one point, with a short headline and a body of one or two sentences. The closing slide \
resolves the topic in its headline and asks the reader an open question in cta."""


class CarouselCover(BaseModel):
    headline: str = Field(description="The hook that makes people swipe, about six words.")
    topic: str | None = Field(
        default=None,
        description="A short uppercase kicker above the hook, such as the subject area. "
        "Up to 60 characters.",
    )
    subline: str | None = Field(
        default=None,
        description="One line under the hook that sets up the carousel, about eight words. "
        "Up to 200 characters.",
    )


class SlideText(BaseModel):
    headline: str = Field(description="The point this slide makes, a few words.")
    body: str | None = Field(
        default=None,
        description="One or two sentences that explain the point, up to 200 characters.",
    )


class CarouselClosing(BaseModel):
    headline: str = Field(description="The resolution or takeaway of the carousel.")
    cta: str | None = Field(
        default=None,
        description="An open question to the reader, up to 200 characters.",
    )


def present(values: Mapping[str, str | None]) -> dict[str, str]:
    return {key: value for key, value in values.items() if value is not None}


def pagination_label(pattern: str) -> Callable[[int, int], str | None]:
    if not pattern:
        return lambda page, pages: None
    try:
        pattern.format(page=1, pages=1)
    except (KeyError, IndexError, ValueError):
        raise AssetsError(
            "pagination may only use the placeholders {page} and {pages}, "
            "for example '{page}/{pages}'."
        ) from None
    return lambda page, pages: pattern.format(page=page, pages=pages)


def card_body(
    *,
    headline: str,
    subline: str | None,
    handle: str | None,
    template: str | None,
    format: str | None,
    extra_fields: Mapping[str, str] | None,
    reserved: Mapping[str, str | None],
) -> dict[str, Any]:
    """The body of one card. reserved maps every key extra_fields must not set to the parameter
    that sets it, or to None when no parameter does."""
    extra = dict(extra_fields or {})
    collisions = sorted(set(extra) & set(reserved))
    if collisions:
        key = collisions[0]
        instead = f" Use the {reserved[key]} parameter instead." if reserved[key] else ""
        raise AssetsError(f"extra_fields must not contain {key!r}.{instead}")
    body: dict[str, Any] = present(
        {
            "template": template,
            "format": format,
            "headline": headline,
            "subline": subline,
            "handle": handle,
        }
    )
    body.update(extra)
    return body


def carousel_pages(slides: Sequence[object]) -> int:
    """The number of slides of a carousel, cover and closing slide included."""
    if not slides:
        raise AssetsError("A carousel needs at least one content slide.")
    pages = len(slides) + 2
    if pages > MAX_CAROUSEL_SLIDES:
        raise AssetsError(
            f"A carousel has at most {MAX_CAROUSEL_SLIDES} slides, "
            "cover and closing slide included."
        )
    return pages


def carousel_bodies(
    *,
    bundle: str,
    cover: CarouselCover,
    slides: Sequence[SlideText],
    closing: CarouselClosing,
    graphics: Sequence[Mapping[str, str] | None],
    label: Callable[[int, int], str | None],
    handle: str | None = None,
    format: str | None = None,
    icon_color: str | None = None,
) -> list[dict[str, str]]:
    """One request body per slide. graphics holds, per content slide, the body keys of its
    image or icon; icon_color goes along with an icon only."""
    pages = len(slides) + 2
    shared = present({"format": format, "handle": handle})
    bodies: list[dict[str, str]] = [
        {
            "template": f"{bundle}/{CAROUSEL_COVER}",
            **present({"headline": cover.headline, "topic": cover.topic, "subline": cover.subline}),
            **shared,
        }
    ]
    for page, (slide, graphic) in enumerate(zip(slides, graphics, strict=True), start=2):
        body = {
            "template": f"{bundle}/{CAROUSEL_CONTENT}",
            **present(
                {"headline": slide.headline, "body": slide.body, "pagination": label(page, pages)}
            ),
            **shared,
        }
        if graphic:
            body.update(graphic)
            if icon_color and any(key.startswith("icon") for key in graphic):
                body["icon_color"] = icon_color
        bodies.append(body)
    bodies.append(
        {
            "template": f"{bundle}/{CAROUSEL_CLOSING}",
            **present(
                {
                    "headline": closing.headline,
                    "cta": closing.cta,
                    "pagination": label(pages, pages),
                }
            ),
            **shared,
        }
    )
    return bodies
