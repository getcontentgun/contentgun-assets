from fastapi import APIRouter, Request

from app import __version__
from app.errors import ApiError
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    pool = getattr(request.app.state, "browser", None)

    if pool is None or not pool.is_ready:
        raise ApiError(503, "Browser not ready.")

    return HealthResponse(
        version=__version__,
        browser="ready",
        templates=request.app.state.templates.ids,
    )
