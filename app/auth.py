import hmac

from fastapi import Header

from app.config import settings
from app.errors import ApiError

UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


async def verify_api_key(authorization: str | None = Header(default=None)) -> str:
    keys = settings.api_key_list
    if not keys:
        raise ApiError(500, "API keys are not configured on the server.")

    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "Unauthorized.", UNAUTHORIZED_HEADERS)

    presented = authorization[len("Bearer ") :].strip()
    if not presented:
        raise ApiError(401, "Unauthorized.", UNAUTHORIZED_HEADERS)

    matched = False
    for key in keys:
        if hmac.compare_digest(presented.encode("utf-8"), key.encode("utf-8")):
            matched = True

    if not matched:
        raise ApiError(401, "Unauthorized.", UNAUTHORIZED_HEADERS)

    return presented
