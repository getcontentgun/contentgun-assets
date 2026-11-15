import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.request_id import HEADER_NAME, request_id_var

logger = logging.getLogger(__name__)

STATUS_MESSAGES = {
    401: "Unauthorized.",
    404: "Not found.",
    405: "Method not allowed.",
    500: "Internal server error.",
    503: "Service unavailable.",
    504: "Render timed out.",
}


class ApiError(Exception):
    def __init__(self, status_code: int, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.headers = headers


def envelope(status_code: int, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status_code, headers=headers)


def as_sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


def validation_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Invalid request."
    first = errors[0]
    if first.get("type") == "json_invalid":
        return "Request body is not valid JSON."
    location = ".".join(str(part) for part in first.get("loc", ()) if part not in ("body", "query"))
    message = first.get("msg", "Invalid value")
    return as_sentence(f"{location}: {message}" if location else message)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return envelope(exc.status_code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return envelope(400, validation_message(exc))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else None
        message = STATUS_MESSAGES.get(exc.status_code) or as_sentence(detail or "Request failed")
        return envelope(exc.status_code, message, exc.headers)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Starlette runs this handler outside the request-id middleware, so both the log
        # context and the header have to be restored here.
        request_id = request.scope.get("state", {}).get("request_id")
        token = request_id_var.set(request_id) if request_id else None
        try:
            logger.exception("Unhandled error: %s: %s", type(exc).__name__, exc)
        finally:
            if token is not None:
                request_id_var.reset(token)
        headers = {HEADER_NAME: request_id} if request_id else None
        return envelope(500, STATUS_MESSAGES[500], headers)
