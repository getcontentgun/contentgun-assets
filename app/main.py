import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app import __version__
from app.browser import BrowserPool
from app.config import settings
from app.errors import register_exception_handlers
from app.images import verify_xml_parser
from app.rendering import validate_defaults
from app.request_id import RequestIdMiddleware, configure_logging
from app.routers import debug, document, health, render, templates
from app.templating import TemplateRegistry

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
    logger.info("Service ready with templates: %s", ", ".join(app.state.templates.ids))
    try:
        yield
    finally:
        await pool.stop()
        app.state.browser = None


def create_app(bundles_dir: Path | None = None) -> FastAPI:
    configure_logging()
    verify_xml_parser()

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
    app.state.templates = TemplateRegistry.load(bundles_dir or settings.bundles_dir)
    app.state.browser = None
    validate_defaults(app.state.templates)

    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(templates.router)
    app.include_router(render.router)
    app.include_router(document.router)
    if settings.debug_routes:
        app.include_router(debug.router)
        logger.warning("Debug routes and the OpenAPI schema are enabled (DEBUG_ROUTES=true).")

    return app


app = create_app()
