import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.browser import BrowserPool
from app.config import settings
from app.errors import register_exception_handlers
from app.files import Files, configured_files
from app.images import verify_xml_parser
from app.reloading import Reloader, service_reloader
from app.request_id import RequestIdMiddleware, configure_logging
from app.routers import admin, debug, document, health, outputs, render, templates, uploads
from app.tenants import TenantDirectory

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = BrowserPool(
        concurrency=settings.render_concurrency,
        render_timeout_ms=settings.render_timeout_ms,
        queue_timeout_ms=settings.queue_timeout_ms,
    )
    try:
        await pool.start()
    except BaseException:
        logger.exception("Chromium could not be started. Run: playwright install chromium")
        raise

    app.state.browser = pool
    directory: TenantDirectory = app.state.tenants
    files: Files | None = app.state.files
    logger.info(
        "Service ready. Tenants: %s. Built-in templates: %s. Storage: %s.",
        ", ".join(directory.names),
        ", ".join(directory.builtin_ids) or "none",
        files.storage.location("") if files is not None else "off",
    )
    try:
        yield
    finally:
        await pool.stop()
        app.state.browser = None
        app.state.reloader.close()


def create_app(tenants: TenantDirectory | None = None, files: Files | None = None) -> FastAPI:
    """The service. Without tenants, they come from the settings, and with storage and no
    BUNDLES_DIR their bundles come from the bucket; without files, the storage of the settings."""
    configure_logging()
    verify_xml_parser()

    if files is None:
        files = configured_files()
    if tenants is None:
        reloader = service_reloader(files)
        tenants = reloader.start()
    else:
        reloader = Reloader(tenants.source)

    app = FastAPI(
        title="contentgun-assets",
        version=__version__,
        description="Renders social media cards from HTML templates.",
        lifespan=lifespan,
        # The schema routes are unauthenticated, so they follow the debug switch.
        docs_url="/docs" if settings.debug_routes else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.debug_routes else None,
    )
    app.state.tenants = tenants
    app.state.files = files
    app.state.reloader = reloader
    app.state.browser = None

    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(templates.router)
    app.include_router(render.router)
    app.include_router(document.router)
    app.include_router(uploads.router)
    app.include_router(outputs.router)
    app.include_router(admin.router)
    if settings.debug_routes:
        app.include_router(debug.router)
        logger.warning("Debug routes and the OpenAPI schema are enabled (DEBUG_ROUTES=true).")

    return app


app = create_app()
