from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from app.errors import ApiError
from app.files import Files

router = APIRouter(tags=["files"])

NOT_FOUND = "Not found."


@router.api_route(
    "/o/{tenant}/{token}/{file_name}",
    methods=["GET", "HEAD"],
    response_class=Response,
    responses={200: {"content": {"image/png": {}, "application/pdf": {}}}},
)
async def output_file(request: Request, tenant: str, token: str, file_name: str) -> Response:
    files: Files | None = request.app.state.files
    if files is None:
        raise ApiError(404, NOT_FOUND)
    stored = await files.find_output(tenant, token, file_name)
    if stored is None:
        raise ApiError(404, NOT_FOUND)

    headers = {
        "Content-Length": str(stored.size),
        "Content-Disposition": f'inline; filename="{file_name}"',
        "Cache-Control": f"public, max-age={files.seconds_left(stored)}, immutable",
        "X-Content-Type-Options": "nosniff",
    }
    # The type follows the file name, never what the bucket says: only PNGs and PDFs go out.
    media_type = files.output_type(file_name)
    if request.method == "HEAD":
        return Response(media_type=media_type, headers=headers)
    chunks = await files.open_output(stored)
    if chunks is None:
        raise ApiError(404, NOT_FOUND)
    return StreamingResponse(chunks, media_type=media_type, headers=headers)
