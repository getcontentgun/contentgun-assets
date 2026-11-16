import logging

from fastapi import APIRouter, Request

from app.auth import CurrentTenant
from app.config import settings
from app.errors import ApiError
from app.files import INVALID_UPLOAD_URL, Files, storage_missing, timestamp
from app.images import readable_size
from app.schemas import UploadResponse, UploadTicketResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["uploads"])


def _files(request: Request) -> Files:
    files: Files | None = request.app.state.files
    if files is None:
        raise storage_missing("uploads")
    return files


@router.post("/v1/uploads", response_model=UploadTicketResponse)
async def create_upload(request: Request, tenant: CurrentTenant) -> UploadTicketResponse:
    ticket = _files(request).new_upload(tenant.name)
    logger.info("Upload URL issued tenant=%s upload_id=%s", tenant.name, ticket.upload_id)
    return UploadTicketResponse(
        upload_id=ticket.upload_id,
        upload_url=ticket.url,
        expires_at=timestamp(ticket.expires_at),
        max_bytes=settings.max_image_bytes,
    )


async def _body(request: Request, limit: int) -> bytes:
    too_large = ApiError(413, f"An upload must be at most {readable_size(limit)}.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_large
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise too_large
    return bytes(body)


@router.put("/u/{token}", status_code=201, response_model=UploadResponse)
async def receive_upload(request: Request, token: str) -> UploadResponse:
    files = _files(request)
    tenant, upload_id = files.check_upload_token(token)
    if request.app.state.tenants.find(tenant) is None:
        raise ApiError(403, INVALID_UPLOAD_URL)
    data = await _body(request, settings.max_image_bytes)
    saved = await files.save_upload(tenant, upload_id, data)
    logger.info(
        "Upload received tenant=%s upload_id=%s kind=%s bytes=%d",
        tenant,
        upload_id,
        saved.kind,
        saved.size,
    )
    return UploadResponse(
        upload_id=saved.upload_id,
        kind=saved.kind,
        content_type=saved.content_type,
        bytes=saved.size,
        expires_at=timestamp(saved.expires_at),
    )
