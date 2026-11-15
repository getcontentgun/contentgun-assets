import os
from pathlib import Path

BUNDLES_DIR = Path(__file__).resolve().parents[1] / "bundles"
BUNDLE = "demo"
CARD = f"{BUNDLE}/card"
CARD_IMAGE = f"{BUNDLE}/card-image"
COVER = f"{BUNDLE}/carousel-cover"
CONTENT = f"{BUNDLE}/carousel-content"
CLOSING = f"{BUNDLE}/carousel-closing"
CAROUSEL = (COVER, CONTENT, CLOSING)
# The faces the bundle's CSS draws: the headlines in the display face, every other text in the
# text face.
DISPLAY_FACE = ("Space Grotesk", 700)
TEXT_FACE = ("Inter", 400)

# Assigned, not setdefault: an exported variable in the developer's shell or a line in .env
# must not silently change what the suite is testing.
os.environ["API_KEYS"] = "test-key,second-key"
os.environ["DEBUG_ROUTES"] = "true"
os.environ["STRICT_FONTS"] = "true"
os.environ["BUNDLES_DIR"] = str(BUNDLES_DIR)
os.environ["DEFAULT_TEMPLATE"] = CARD

import base64  # noqa: E402
import shutil  # noqa: E402
import tomllib  # noqa: E402
from io import BytesIO  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.main import create_app  # noqa: E402

API_KEY = "test-key"
AUTH = {"Authorization": f"Bearer {API_KEY}"}


def image_base64(
    size: tuple[int, int] = (4, 4),
    colour: tuple[int, int, int] = (255, 255, 255),
    fmt: str = "PNG",
) -> str:
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, format=fmt)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


SQUARE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
    '<rect width="16" height="16"/></svg>'
)


def svg_base64(markup: str = SQUARE_SVG) -> str:
    return base64.b64encode(markup.encode("utf-8")).decode("ascii")


TEMPLATE_HEAD = 'formats = ["4:5"]\nfields = ["headline", "note"]\nrequired = ["headline"]\n'
TEMPLATE_SAMPLES = '[samples]\nheadline = "Hallo"\n'
MINIMAL_TEMPLATE = TEMPLATE_HEAD + TEMPLATE_SAMPLES


def template_toml(settings: str = "", samples: str = TEMPLATE_SAMPLES) -> str:
    """A template.toml with further top-level settings, which TOML only takes before the first
    table."""
    return TEMPLATE_HEAD + settings + samples


def write_template(
    bundle_dir: Path, name: str, toml: str = MINIMAL_TEMPLATE, html: str = "{{ fields.headline }}"
) -> Path:
    directory = bundle_dir / name
    directory.mkdir(parents=True)
    (directory / "template.toml").write_text(f'name = "{name}"\n{toml}', encoding="utf-8")
    (directory / "template.html.j2").write_text(html, encoding="utf-8")
    return directory


def source_face() -> dict:
    """The first font face of the bundle the suite tests, whose file every small bundle
    borrows."""
    raw = tomllib.loads((BUNDLES_DIR / BUNDLE / "bundle.toml").read_text(encoding="utf-8"))
    return raw["fonts"]["faces"][0]


def write_bundle(
    root: Path,
    name: str = "mini",
    templates: dict[str, str | tuple[str, str]] | None = None,
    family: str = "Mini Sans",
) -> Path:
    """A small bundle under root: one font face, under a family name of its own, and the given
    templates, each a name with its template.toml, or with its template.toml and its
    template.html.j2."""
    face = source_face()
    weight = str(face["weight"]).split()[0]
    directory = root / name
    (directory / "fonts").mkdir(parents=True)
    shutil.copyfile(
        BUNDLES_DIR / BUNDLE / "fonts" / face["file"], directory / "fonts" / "face.woff2"
    )
    (directory / "bundle.toml").write_text(
        f'name = "{name}"\n'
        'description = "A bundle of the test suite."\n'
        "\n"
        "[fonts]\n"
        f"checks = ['{weight} 20px \"{family}\"']\n"
        "\n"
        "[[fonts.faces]]\n"
        f'family = "{family}"\n'
        'file = "face.woff2"\n'
        f'weight = "{face["weight"]}"\n',
        encoding="utf-8",
    )
    if templates is None:
        templates = {"plain": MINIMAL_TEMPLATE}
    for template, source in templates.items():
        toml, html = source if isinstance(source, tuple) else (source, "{{ fields.headline }}")
        write_template(directory, template, toml, html)
    return directory


# What the renderer offers beyond the bundle the suite tests: a template's own [limits], the
# highlight and percent filters, and the 9:16 format.
SHOWCASE = "mini/showcase"
SHOWCASE_TEMPLATE = (
    'formats = ["9:16", "4:5"]\n'
    'fields = ["headline", "highlight", "badge", "focus_x"]\n'
    'required = ["headline"]\n'
    "[limits]\nheadline = 35\nbadge = 15\nfocus_x = 4\n"
    '[defaults]\nhighlight = ""\nbadge = ""\nfocus_x = "50"\n'
    '[samples]\nheadline = "Gute Karten"\nhighlight = "Karten"\n'
)
SHOWCASE_HTML = (
    '<main style="--w: {{ width }}px; --h: {{ height }}px; '
    '--focus-x: {{ fields.focus_x | percent }}%;">'
    "{{ fields.headline | highlight(fields.highlight) }}</main>"
)


@pytest.fixture(scope="module")
def showcase_root(tmp_path_factory) -> Path:
    """The bundles of the suite next to a small one with SHOWCASE, so DEFAULT_TEMPLATE still
    resolves."""
    root = tmp_path_factory.mktemp("bundles")
    (root / BUNDLE).symlink_to(BUNDLES_DIR / BUNDLE)
    write_bundle(root, templates={"showcase": (SHOWCASE_TEMPLATE, SHOWCASE_HTML)})
    return root


@pytest.fixture(scope="module")
def showcase_app(showcase_root):
    return create_app(showcase_root)


@pytest.fixture
async def showcase_client(showcase_app):
    transport = ASGITransport(app=showcase_app)
    async with AsyncClient(transport=transport, base_url="http://test") as instance:
        yield instance


MISSING_BROWSER_MARKERS = (
    "executable doesn't exist",
    "please run the following command to download new browsers",
    "playwright install",
)


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as instance:
        yield instance


# Explicit loop_scope so the suite does not depend on a pytest-asyncio ini default:
# a module-scoped fixture and function-scoped tests would otherwise be on different loops.
@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live_client(app):
    manager = LifespanManager(app, startup_timeout=60)
    try:
        await manager.__aenter__()
    except Exception as exc:
        message = str(exc).lower()
        if not any(marker in message for marker in MISSING_BROWSER_MARKERS):
            raise
        pytest.skip(f"Playwright Chromium is not installed: {exc}")

    try:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(transport=transport, base_url="http://test") as instance:
            yield instance
    finally:
        await manager.__aexit__(None, None, None)
