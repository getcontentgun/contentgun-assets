from fastapi import APIRouter, Depends, Request

from app.auth import internal_access
from app.schemas import ReloadResponse, TenantSummary

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(internal_access)])


@router.post("/reload", response_model=ReloadResponse)
async def reload(request: Request) -> ReloadResponse:
    directory = await request.app.state.reloader.reload(request.app)
    return ReloadResponse(
        tenants=[
            TenantSummary(name=tenant.name, templates=len(tenant.registry.ids))
            for tenant in directory.tenants
        ]
    )
