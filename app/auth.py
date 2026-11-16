from typing import Annotated

from fastapi import Depends, Header, Request

from app.errors import ApiError
from app.tenants import Internal, Tenant, TenantDirectory

TENANT_HEADER = "X-Contentgun-Tenant"
UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "Unauthorized.", UNAUTHORIZED_HEADERS)
    presented = authorization[len("Bearer ") :].strip()
    if not presented:
        raise ApiError(401, "Unauthorized.", UNAUTHORIZED_HEADERS)
    return presented


async def current_tenant(
    request: Request,
    authorization: str | None = Header(default=None),
    tenant_header: str | None = Header(default=None, alias=TENANT_HEADER),
) -> Tenant:
    directory: TenantDirectory = request.app.state.tenants
    if not directory.has_keys:
        raise ApiError(500, "API keys are not configured on the server.")

    match = directory.authenticate(_bearer(authorization))
    if match is None:
        raise ApiError(401, "Unauthorized.", UNAUTHORIZED_HEADERS)
    if match is not Internal.ACCESS:
        return match

    name = (tenant_header or "").strip()
    if not name:
        raise ApiError(400, f"The internal key needs the header {TENANT_HEADER}.")
    tenant = directory.find(name)
    if tenant is None:
        raise ApiError(400, f"The header {TENANT_HEADER} names no known tenant.")
    return tenant


CurrentTenant = Annotated[Tenant, Depends(current_tenant)]


async def internal_access(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    """The internal key alone, for routes that act on the whole service rather than on a
    tenant."""
    directory: TenantDirectory = request.app.state.tenants
    if directory.authenticate(_bearer(authorization)) is not Internal.ACCESS:
        raise ApiError(401, "Unauthorized.", UNAUTHORIZED_HEADERS)
