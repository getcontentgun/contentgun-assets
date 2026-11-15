"""Pins the relationship between the character limits and the minimum font sizes.

The caps in app/schemas.py are meant to be tight enough that running text never reaches a
floor, and that even the tallest text a field can hold, an unbroken run of wide capitals,
still fits at the floor, so a card is never silently clipped. Every template of the bundle the
suite tests is measured in every format it offers. Raising a cap, shrinking a box or switching
a font without re-checking would break that quietly, since the endpoint still answers 200.
"""

import pytest
import pytest_asyncio

from app.formats import Format, dimensions
from app.rendering import resolve_fields, resolve_template
from app.schemas import MAX_FIELD_LENGTH, RenderRequest
from app.templating import TemplateRegistry
from tests.conftest import BUNDLES_DIR, CONTENT, image_base64, svg_base64

pytestmark = [pytest.mark.requires_chromium, pytest.mark.asyncio(loop_scope="module")]

# The vertical check compares rectangles because scrollHeight misses the half of an overflow
# that a centered stage pushes above its top edge.
OVERFLOW_PROBE = """
(names) => {
  const root = getComputedStyle(document.documentElement);
  const stage = document.querySelector('.stage');
  const bounds = stage.getBoundingClientRect();
  const items = Array.from(stage.children)
    .filter((child) => getComputedStyle(child).position !== 'absolute')
    .map((child) => child.getBoundingClientRect());
  const top = Math.min(...items.map((item) => item.top));
  const bottom = Math.max(...items.map((item) => item.bottom));
  const graphic = document.querySelector('.graphic');
  const result = {
    stage: {
      overflowsVertically: top < bounds.top - 1 || bottom > bounds.bottom + 1,
      overflowsHorizontally: stage.scrollWidth > stage.clientWidth + 1,
    },
    layout: {
      gapAbove: top - bounds.top,
      gapBelow: bounds.bottom - bottom,
      graphicHeight: graphic ? graphic.getBoundingClientRect().height : null,
      iconSize: parseFloat(root.getPropertyValue('--icon-size')),
    },
  };
  for (const name of names) {
    const element = document.querySelector(`[data-fit="${name}"]`);
    if (!element) {
      result[name] = null;
      continue;
    }
    const box = element.parentElement;
    result[name] = {
      size: parseFloat(getComputedStyle(element).fontSize),
      floor: parseFloat(root.getPropertyValue(`--${name}-min`)),
      clippedVertically: element.scrollHeight > box.clientHeight + 1,
      clippedHorizontally: element.scrollWidth > box.clientWidth + 1,
    };
  }
  return result;
}
"""

META_PROBE = """
() => {
  const meta = document.querySelector('.meta');
  const pagination = document.querySelector('.pagination');
  return {
    rowOverflows: meta.scrollWidth > meta.clientWidth + 1,
    paginationClipped: pagination.scrollWidth > pagination.clientWidth + 1,
    paginationGap: meta.getBoundingClientRect().right - pagination.getBoundingClientRect().right,
  };
}
"""

IMAGE_PROBE = """
() => {
  const image = document.querySelector('.graphic');
  const box = image.getBoundingClientRect();
  const root = getComputedStyle(document.documentElement);
  return {
    width: box.width,
    height: box.height,
    maxHeight: parseFloat(root.getPropertyValue('--image-max-height')),
    stageWidth: document.querySelector('.stage').clientWidth,
  };
}
"""

REGISTRY = TemplateRegistry.load(BUNDLES_DIR)
FORMATS = [(spec.id, fmt) for spec in REGISTRY.specs for fmt in spec.formats]
COPY_FIELDS = ("subline", "body", "cta")
COPY = [
    (template, fmt, field)
    for template, fmt in FORMATS
    for field in REGISTRY.get(template).fields
    if field in COPY_FIELDS
]
WITH_PAGINATION = [
    (template, fmt) for template, fmt in FORMATS if "pagination" in REGISTRY.get(template).fields
]
CONTENT_FORMATS = REGISTRY.get(CONTENT).formats
# Fields that are not running text: the handle and the pagination never wrap, and icon_color
# picks a color.
NOT_TEXT = frozenset({"handle", "pagination", "icon_color"})

HEADLINE_READABLE = 45
COPY_READABLE = 26

ICON = f"data:image/svg+xml;base64,{svg_base64()}"
GRAPHICS = {
    "icon": {"icon": ICON},
    "tall-image": {"image": f"data:image/png;base64,{image_base64((600, 3000))}"},
}

# hyphenate() chops a run without break points every 12 characters, and at the floor sizes a
# chunk of these capitals fills a line on its own, so they are the tallest text a field holds.
UNBROKEN_RUNS = ["A", "M", "W"]


def case_id(value) -> str | None:
    return value.value if isinstance(value, Format) else None


def limit_of(field: str, template: str | None = None) -> int:
    if template is not None and field in REGISTRY.get(template).limits:
        return REGISTRY.get(template).limits[field]
    typed = RenderRequest.model_fields.get(field)
    for item in typed.metadata if typed else ():
        if hasattr(item, "max_length"):
            return item.max_length
    return MAX_FIELD_LENGTH


def text_fields(template: str, copy: bool = True) -> list[str]:
    return [
        field
        for field in REGISTRY.get(template).fields
        if field not in NOT_TEXT and (copy or field not in COPY_FIELDS)
    ]


def filled(length: int) -> str:
    return ("Automatisierung " * 40)[:length].strip()


def assert_fits(measured: dict | None, readable: int | None = None) -> None:
    assert measured is not None
    assert measured["clippedVertically"] is False
    assert measured["clippedHorizontally"] is False
    assert measured["size"] >= measured["floor"]
    if readable is not None:
        assert measured["size"] > readable


def assert_copy_fits(result: dict, template: str, readable: int | None = None) -> None:
    for field in text_fields(template):
        if field in COPY_FIELDS:
            assert_fits(result[field], readable)


def assert_stage_holds(result: dict) -> None:
    assert result["stage"] == {"overflowsVertically": False, "overflowsHorizontally": False}


def assert_centered(result: dict) -> None:
    assert abs(result["layout"]["gapAbove"] - result["layout"]["gapBelow"]) <= 1


def assert_graphic_keeps_its_room(result: dict) -> None:
    assert result["layout"]["graphicHeight"] >= result["layout"]["iconSize"]


@pytest.fixture(scope="module")
def registry():
    return REGISTRY


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def page():
    from playwright.async_api import async_playwright

    from app.browser import LAUNCH_ARGS

    try:
        playwright = await async_playwright().start()
    except Exception as exc:  # pragma: no cover - depends on the local installation
        pytest.skip(f"Playwright is unavailable: {exc}")

    try:
        browser = await playwright.chromium.launch(headless=True, args=LAUNCH_ARGS)
    except Exception as exc:  # pragma: no cover - depends on the local installation
        await playwright.stop()
        pytest.skip(f"Chromium is not installed: {exc}")

    context = await browser.new_context(
        viewport={"width": 1080, "height": 1350}, device_scale_factor=1, locale="de-DE"
    )
    instance = await context.new_page()
    try:
        yield instance
    finally:
        await context.close()
        await browser.close()
        await playwright.stop()


async def load(
    page,
    registry,
    template: str,
    fmt: Format,
    requested: dict,
    image: str | None = None,
    icon: str | None = None,
) -> None:
    spec = resolve_template(registry, template)
    fields = resolve_fields(spec, requested)
    html = registry.render_html(spec, fmt, fields, image=image, icon=icon)
    width, height = dimensions(fmt)
    await page.set_viewport_size({"width": width, "height": height})
    await page.set_content(html, wait_until="load")
    await page.wait_for_function("document.documentElement.dataset.ready === '1'")


async def measure(
    page,
    registry,
    template: str,
    fmt: Format,
    image: str | None = None,
    icon: str | None = None,
    **requested,
):
    await load(page, registry, template, fmt, requested, image=image, icon=icon)
    return await page.evaluate(OVERFLOW_PROBE, list(requested))


async def test_template_driven_field_cap_matches_the_schema():
    # resolve_fields applies MAX_FIELD_LENGTH to fields a template declares without a
    # typed counterpart, so it must not be looser than the typed limits it backs up.
    for field in ("headline", "subline", "handle", "topic", "pagination"):
        assert limit_of(field) <= MAX_FIELD_LENGTH


@pytest.mark.parametrize(("template", "fmt"), FORMATS, ids=case_id)
async def test_running_text_at_every_limit_fits_and_stays_readable(page, registry, template, fmt):
    requested = {field: filled(limit_of(field, template)) for field in text_fields(template)}
    result = await measure(page, registry, template, fmt, **requested)
    assert_fits(result["headline"], HEADLINE_READABLE)
    assert_copy_fits(result, template, COPY_READABLE)
    assert_stage_holds(result)


@pytest.mark.parametrize(("template", "fmt"), FORMATS, ids=case_id)
async def test_the_headline_at_its_limit_stays_readable_next_to_unfitted_fields(
    page, registry, template, fmt
):
    # Without the copy, but with a field that is not fitted, such as the cover's topic, which
    # takes its room from the headline.
    fields = text_fields(template, copy=False)
    requested = {field: filled(limit_of(field, template)) for field in fields}
    result = await measure(page, registry, template, fmt, **requested)
    assert_fits(result["headline"], HEADLINE_READABLE)
    assert_stage_holds(result)


@pytest.mark.parametrize(("template", "fmt", "field"), COPY, ids=case_id)
async def test_the_copy_at_its_limit_stays_readable(page, registry, template, fmt, field):
    requested = {"headline": "Kurz", field: filled(limit_of(field, template))}
    result = await measure(page, registry, template, fmt, **requested)
    assert_fits(result[field], COPY_READABLE)
    assert_stage_holds(result)


@pytest.mark.parametrize("run", UNBROKEN_RUNS)
@pytest.mark.parametrize(("template", "fmt"), FORMATS, ids=case_id)
async def test_unbroken_runs_at_every_limit_are_not_clipped(page, registry, template, fmt, run):
    requested = {field: run * limit_of(field, template) for field in text_fields(template)}
    result = await measure(page, registry, template, fmt, **requested)
    assert_fits(result["headline"])
    assert_copy_fits(result, template)
    assert_stage_holds(result)


@pytest.mark.parametrize("run", UNBROKEN_RUNS)
@pytest.mark.parametrize(("template", "fmt"), FORMATS, ids=case_id)
async def test_unbroken_runs_without_the_copy_are_not_clipped(page, registry, template, fmt, run):
    fields = text_fields(template, copy=False)
    requested = {field: run * limit_of(field, template) for field in fields}
    result = await measure(page, registry, template, fmt, **requested)
    assert_fits(result["headline"])
    assert_stage_holds(result)


@pytest.mark.parametrize(("template", "fmt"), WITH_PAGINATION, ids=case_id)
async def test_pagination_at_its_limit_stays_on_the_handle_row(page, registry, template, fmt):
    # The handle gives way with an ellipsis; the pagination must stay whole at the right edge.
    requested = {
        "headline": "Kurz",
        "pagination": "W" * limit_of("pagination"),
        "handle": "W" * limit_of("handle"),
    }
    await load(page, registry, template, fmt, requested)
    result = await page.evaluate(META_PROBE)
    assert result["rowOverflows"] is False
    assert result["paginationClipped"] is False
    assert abs(result["paginationGap"]) <= 1


@pytest.mark.parametrize("fmt", CONTENT_FORMATS, ids=case_id)
async def test_content_text_is_vertically_centered(page, registry, fmt):
    spec = registry.get(CONTENT)
    result = await measure(page, registry, CONTENT, fmt, **spec.samples)
    assert_centered(result)
    assert result["layout"]["gapAbove"] > 0


@pytest.mark.parametrize("graphic", GRAPHICS)
@pytest.mark.parametrize("fmt", CONTENT_FORMATS, ids=case_id)
async def test_content_with_a_graphic_fits_both_fields_at_their_limits(
    page, registry, fmt, graphic
):
    result = await measure(
        page,
        registry,
        CONTENT,
        fmt,
        **GRAPHICS[graphic],
        headline=filled(limit_of("headline")),
        body=filled(MAX_FIELD_LENGTH),
    )
    assert_fits(result["headline"], HEADLINE_READABLE)
    assert_fits(result["body"], COPY_READABLE)
    assert_stage_holds(result)
    assert_centered(result)
    assert_graphic_keeps_its_room(result)


@pytest.mark.parametrize("run", UNBROKEN_RUNS)
@pytest.mark.parametrize("graphic", GRAPHICS)
@pytest.mark.parametrize("fmt", CONTENT_FORMATS, ids=case_id)
async def test_content_with_a_graphic_keeps_unbroken_runs_unclipped(
    page, registry, fmt, graphic, run
):
    result = await measure(
        page,
        registry,
        CONTENT,
        fmt,
        **GRAPHICS[graphic],
        headline=run * limit_of("headline"),
        body=run * MAX_FIELD_LENGTH,
    )
    assert_fits(result["headline"])
    assert_fits(result["body"])
    assert_stage_holds(result)
    assert_graphic_keeps_its_room(result)


@pytest.mark.parametrize("size", [(3000, 1000), (1600, 900), (600, 3000), (120, 80)], ids=str)
@pytest.mark.parametrize("fmt", CONTENT_FORMATS, ids=case_id)
async def test_content_image_keeps_its_proportions_and_never_grows(page, registry, fmt, size):
    spec = registry.get(CONTENT)
    image = f"data:image/png;base64,{image_base64(size)}"
    await load(page, registry, CONTENT, fmt, spec.samples, image=image)
    box = await page.evaluate(IMAGE_PROBE)
    assert abs(box["width"] - box["height"] * size[0] / size[1]) <= 1
    assert box["width"] <= size[0]
    assert box["height"] <= size[1]
    assert box["width"] <= box["stageWidth"]
    assert box["height"] <= box["maxHeight"]


@pytest.mark.parametrize(("fmt", "height"), [(Format.PORTRAIT_4_5, 300), (Format.SQUARE, 225)])
async def test_a_large_content_image_takes_the_full_image_height(page, registry, fmt, height):
    spec = registry.get(CONTENT)
    image = f"data:image/png;base64,{image_base64((1600, 900))}"
    await load(page, registry, CONTENT, fmt, spec.samples, image=image)
    box = await page.evaluate(IMAGE_PROBE)
    assert box["height"] == pytest.approx(height, abs=0.5)
