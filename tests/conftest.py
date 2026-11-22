import os
from pathlib import Path

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "bundles"
BUNDLE = "demo"
CARD = f"{BUNDLE}/card"
CARD_IMAGE = f"{BUNDLE}/card-image"
COVER = f"{BUNDLE}/carousel-cover"
CONTENT = f"{BUNDLE}/carousel-content"
CLOSING = f"{BUNDLE}/carousel-closing"
CAROUSEL = (COVER, CONTENT, CLOSING)
QUOTE = f"{BUNDLE}/quote"
# The faces the bundle's CSS draws: the headlines in the display face, every other text in the
# text face.
DISPLAY_FACE = ("Space Grotesk", 700)
TEXT_FACE = ("Inter", 400)
API_KEYS = ("test-key", "second-key")

# Assigned, not setdefault: an exported variable in the developer's shell or a line in .env
# must not silently change what the suite is testing. A blank value counts as unset.
os.environ["API_KEYS"] = ",".join(API_KEYS)
os.environ["TENANTS_FILE"] = ""
os.environ["INTERNAL_KEY"] = ""
os.environ["BUNDLES_DIR"] = ""
os.environ["DEBUG_ROUTES"] = "true"
os.environ["STRICT_FONTS"] = "true"
os.environ["DEFAULT_TEMPLATE"] = CARD
for variable in (
    "S3_ENDPOINT",
    "S3_REGION",
    "S3_BUCKET",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "PUBLIC_FILES_URL",
):
    os.environ[variable] = ""
os.environ["STORAGE_TTL_HOURS"] = "24"

import base64  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import tomllib  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from io import BytesIO  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.files import Files  # noqa: E402
from app.main import create_app  # noqa: E402
from app.templating import TemplateRegistry  # noqa: E402
from app.tenants import SHORT_FORM_TENANT, TenantDirectory, load_tenants  # noqa: E402
from tests.memory_storage import MemoryStorage  # noqa: E402

API_KEY = API_KEYS[0]
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

# The least a page needs for a screenshot: the bundle's fonts loaded and the readiness gate set.
READY_PAGE = (
    "<!doctype html><html><head><style>{{ font_css }}</style></head>"
    "<body><h1>{{ fields.headline }}</h1><script>"
    "var ready = function () { document.documentElement.dataset.ready = '1'; };"
    "Promise.all({{ font_checks | tojson }}.map(function (face) {"
    " return document.fonts.load(face); })).then(ready, ready);"
    "</script></body></html>"
)


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
    raw = tomllib.loads((BUILTIN_DIR / BUNDLE / "bundle.toml").read_text(encoding="utf-8"))
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
        BUILTIN_DIR / BUNDLE / "fonts" / face["file"], directory / "fonts" / "face.woff2"
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


def key_hash(key: str) -> str:
    return f"sha256:{hashlib.sha256(key.encode('utf-8')).hexdigest()}"


def write_tenants_file(path: Path, tenants: dict[str, dict[str, str | list[str]]]) -> Path:
    """A tenants file with one table per tenant. JSON strings are valid TOML strings."""
    lines = []
    for name, table in tenants.items():
        lines.append(f"[tenants.{json.dumps(name)}]")
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in table.items())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def tenants_for(
    builtin_dir: Path = BUILTIN_DIR,
    bundles_dir: Path | None = None,
    tenants_file: Path | None = None,
    api_keys: tuple[str, ...] | None = None,
    internal_key: str = "",
    default_template: str = CARD,
) -> TenantDirectory:
    """The tenants as the service loads them; without a tenants file, from the keys of the suite."""
    if api_keys is None:
        api_keys = API_KEYS if tenants_file is None else ()
    return load_tenants(
        builtin_dir=builtin_dir,
        bundles_dir=bundles_dir,
        tenants_file=tenants_file,
        api_keys=api_keys,
        internal_key=internal_key,
        default_template=default_template,
    )


def registry_of(app: FastAPI, tenant: str = SHORT_FORM_TENANT) -> TemplateRegistry:
    return app.state.tenants.get(tenant).registry


@asynccontextmanager
async def client_of(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as instance:
        yield instance


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
    (root / BUNDLE).symlink_to(BUILTIN_DIR / BUNDLE)
    write_bundle(root, templates={"showcase": (SHOWCASE_TEMPLATE, SHOWCASE_HTML)})
    return root


@pytest.fixture(scope="module")
def showcase_app(showcase_root):
    return create_app(tenants_for(builtin_dir=showcase_root))


@pytest.fixture
async def showcase_client(showcase_app):
    async with client_of(showcase_app) as instance:
        yield instance


# Three tenants with a bundle of their own each: acme and studio see the built-in bundles next to
# theirs, solo lists its own bundle only and so names a default template of its own.
ACME, STUDIO, SOLO = "acme", "studio", "solo"
ACME_CARD, STUDIO_CARD, SOLO_CARD = "acme-brand/card", "studio-cards/card", "solo-cards/card"
TENANT_KEYS = {ACME: "acme-key", STUDIO: "studio-key", SOLO: "solo-key"}
INTERNAL_KEY = "internal-key-of-the-test-suite-0123456789"


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def tenant_auth(tenant: str) -> dict[str, str]:
    return bearer(TENANT_KEYS[tenant])


def write_tenants(root: Path) -> tuple[Path, Path]:
    """The bundle folders and the tenants file of the three tenants, under root."""
    bundles = root / "bundles"
    for tenant, template_id in ((ACME, ACME_CARD), (STUDIO, STUDIO_CARD), (SOLO, SOLO_CARD)):
        bundle, template = template_id.split("/")
        write_bundle(bundles / tenant, bundle, templates={template: (MINIMAL_TEMPLATE, READY_PAGE)})
    tenants_file = write_tenants_file(
        root / "tenants.toml",
        {
            ACME: {"api_keys": [key_hash(TENANT_KEYS[ACME])], "emails": ["team@acme.example"]},
            STUDIO: {"api_keys": [key_hash(TENANT_KEYS[STUDIO])]},
            SOLO: {
                "api_keys": [key_hash(TENANT_KEYS[SOLO])],
                "bundles": [SOLO_CARD.split("/")[0]],
                "default_template": SOLO_CARD,
            },
        },
    )
    return bundles, tenants_file


@pytest.fixture(scope="module")
def tenant_app(tmp_path_factory):
    bundles, tenants_file = write_tenants(tmp_path_factory.mktemp("tenants"))
    return create_app(
        tenants_for(bundles_dir=bundles, tenants_file=tenants_file, internal_key=INTERNAL_KEY)
    )


@pytest.fixture
async def tenant_client(tenant_app):
    async with client_of(tenant_app) as instance:
        yield instance


# The storage of the suite: a dict behind the storage interface, reached at FILES_URL.
FILES_URL = "http://files.test"
FILES_SECRET = "secret-key-of-the-test-storage"


def files_of(storage: MemoryStorage) -> Files:
    return Files(storage, FILES_URL, 24, FILES_SECRET)


@pytest.fixture(scope="module")
def storage_app(tmp_path_factory):
    """The three tenants of tenant_app, with storage."""
    bundles, tenants_file = write_tenants(tmp_path_factory.mktemp("storage-tenants"))
    return create_app(
        tenants_for(bundles_dir=bundles, tenants_file=tenants_file, internal_key=INTERNAL_KEY),
        files=files_of(MemoryStorage()),
    )


@pytest.fixture
def storage(storage_app) -> MemoryStorage:
    """The storage of storage_app, emptied and working for every test."""
    storage = storage_app.state.files.storage
    storage.objects.clear()
    storage.failing = False
    return storage


@pytest.fixture
async def storage_client(storage_app, storage):
    async with client_of(storage_app) as instance:
        yield instance


def pytest_addoption(parser):
    parser.addoption(
        "--require-chromium",
        action="store_true",
        help="Fail a test marked requires_chromium instead of skipping it.",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if (
        report.skipped
        and item.config.getoption("--require-chromium")
        and item.get_closest_marker("requires_chromium")
    ):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else report.longrepr
        report.outcome = "failed"
        report.longrepr = f"Skipped although --require-chromium is set: {reason}"


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
    async with client_of(app) as instance:
        yield instance


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A client of the app with its lifespan, and so its browser, started."""
    manager = LifespanManager(app, startup_timeout=60)
    try:
        await manager.__aenter__()
    except Exception as exc:
        message = str(exc).lower()
        if not any(marker in message for marker in MISSING_BROWSER_MARKERS):
            raise
        pytest.skip(f"Playwright Chromium is not installed: {exc}")

    try:
        async with client_of(manager.app) as instance:
            yield instance
    finally:
        await manager.__aexit__(None, None, None)


# Explicit loop_scope so the suite does not depend on a pytest-asyncio ini default:
# a module-scoped fixture and function-scoped tests would otherwise be on different loops.
@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live_client(app):
    async with running(app) as instance:
        yield instance


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def tenant_live_client(tenant_app):
    async with running(tenant_app) as instance:
        yield instance
