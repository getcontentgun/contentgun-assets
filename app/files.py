import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, TypeVar

from app.errors import ApiError
from app.images import classify_upload
from app.storage import Storage, StorageError, StoredObject, s3_storage, storage_settings
from app.templating import NAME

T = TypeVar("T")

Kind = Literal["image", "icon"]

OUTPUTS = "outputs"
UPLOADS = "uploads"
EXPIRES = "expires-at"
KIND = "kind"
UPLOAD_URL_SECONDS = 15 * 60
OUTPUT_TOKEN = re.compile(r"[A-Za-z0-9_-]{32}")
OUTPUT_TYPES = {".png": "image/png", ".pdf": "application/pdf"}
OUTPUT_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,99}\.(?:png|pdf)")
UPLOAD_ID = re.compile(r"up_[A-Za-z0-9_-]{24}")
SIGNATURE = re.compile(r"[A-Za-z0-9_-]{43}")
SECONDS = re.compile(r"[0-9]{1,12}")
TOKEN_CONTEXT = b"contentgun-assets upload urls v1"
UNAVAILABLE = "The file storage is not available, retry later."
INVALID_UPLOAD_URL = "The upload URL is not valid."


def now() -> float:
    return time.time()


def timestamp(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def storage_missing(feature: str) -> ApiError:
    return ApiError(501, f"Storage is not configured on this server, so {feature} cannot be used.")


async def _run(function: Callable[..., T], *args) -> T:
    try:
        return await asyncio.to_thread(function, *args)
    except StorageError:
        raise ApiError(503, UNAVAILABLE) from None


def _expired(stored: StoredObject) -> bool:
    raw = stored.metadata.get(EXPIRES, "")
    # An object without a readable expiry was not written by the service and is never served.
    return not SECONDS.fullmatch(raw) or int(raw) <= now()


@dataclass(frozen=True)
class SavedOutput:
    url: str
    expires_at: int
    size: int


@dataclass(frozen=True)
class UploadTicket:
    upload_id: str
    url: str
    expires_at: int


@dataclass(frozen=True)
class SavedUpload:
    upload_id: str
    kind: Kind
    content_type: str
    size: int
    expires_at: int


class Files:
    """Outputs and uploads in the bucket: where they lie, how long they live, and the signed
    URLs that let a caller without a key upload one file."""

    def __init__(self, storage: Storage, public_url: str, ttl_hours: int, secret: str):
        self.storage = storage
        self.ttl_hours = ttl_hours
        self._public_url = public_url.rstrip("/")
        self._key = hmac.new(secret.encode("utf-8"), TOKEN_CONTEXT, hashlib.sha256).digest()

    @property
    def _ttl_seconds(self) -> int:
        return self.ttl_hours * 3600

    async def save_output(
        self, tenant: str, file_name: str, data: bytes, content_type: str
    ) -> SavedOutput:
        token = secrets.token_urlsafe(24)
        expires = int(now()) + self._ttl_seconds
        key = f"{OUTPUTS}/{tenant}/{token}/{file_name}"
        await _run(self.storage.put, key, data, content_type, {EXPIRES: str(expires)})
        url = f"{self._public_url}/o/{tenant}/{token}/{file_name}"
        return SavedOutput(url=url, expires_at=expires, size=len(data))

    async def find_output(self, tenant: str, token: str, file_name: str) -> StoredObject | None:
        if not (
            NAME.fullmatch(tenant)
            and OUTPUT_TOKEN.fullmatch(token)
            and OUTPUT_NAME.fullmatch(file_name)
        ):
            return None
        stored = await _run(self.storage.head, f"{OUTPUTS}/{tenant}/{token}/{file_name}")
        if stored is None or _expired(stored):
            return None
        return stored

    async def open_output(self, stored: StoredObject) -> Iterator[bytes] | None:
        opened = await _run(self.storage.open, stored.key)
        return None if opened is None else opened[1]

    @staticmethod
    def output_type(file_name: str) -> str:
        return OUTPUT_TYPES[file_name[file_name.rfind(".") :]]

    @staticmethod
    def seconds_left(stored: StoredObject) -> int:
        return max(0, int(stored.metadata[EXPIRES]) - int(now()))

    def _signature(self, message: str) -> str:
        digest = hmac.new(self._key, message.encode("ascii"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def new_upload(self, tenant: str) -> UploadTicket:
        upload_id = f"up_{secrets.token_urlsafe(18)}"
        expires = int(now()) + UPLOAD_URL_SECONDS
        message = f"{tenant}.{upload_id}.{expires}"
        url = f"{self._public_url}/u/{message}.{self._signature(message)}"
        return UploadTicket(upload_id=upload_id, url=url, expires_at=expires)

    def check_upload_token(self, token: str) -> tuple[str, str]:
        """The tenant and the upload id a token names, once its signature and its expiry are
        checked."""
        parts = token.split(".")
        if len(parts) != 4:
            raise ApiError(403, INVALID_UPLOAD_URL)
        tenant, upload_id, expires, signature = parts
        if not (
            NAME.fullmatch(tenant)
            and UPLOAD_ID.fullmatch(upload_id)
            and SECONDS.fullmatch(expires)
            and SIGNATURE.fullmatch(signature)
        ):
            raise ApiError(403, INVALID_UPLOAD_URL)
        expected = self._signature(f"{tenant}.{upload_id}.{expires}")
        if not hmac.compare_digest(expected, signature):
            raise ApiError(403, INVALID_UPLOAD_URL)
        if int(expires) <= now():
            raise ApiError(
                403, "The upload URL has expired. Ask for a new one with POST /v1/uploads."
            )
        return tenant, upload_id

    async def save_upload(self, tenant: str, upload_id: str, data: bytes) -> SavedUpload:
        kind, content_type = classify_upload(data)
        key = f"{UPLOADS}/{tenant}/{upload_id}"
        if await _run(self.storage.head, key) is not None:
            raise ApiError(
                409,
                "The upload URL has been used already. Ask for a new one with POST /v1/uploads.",
            )
        expires = int(now()) + self._ttl_seconds
        await _run(self.storage.put, key, data, content_type, {EXPIRES: str(expires), KIND: kind})
        return SavedUpload(
            upload_id=upload_id,
            kind=kind,
            content_type=content_type,
            size=len(data),
            expires_at=expires,
        )

    def uploads_of(self, tenant: str) -> "TenantUploads":
        return TenantUploads(self, tenant)


class TenantUploads:
    """The uploads one tenant may use in a request: its own, unexpired, of the right kind."""

    def __init__(self, files: Files, tenant: str):
        self._files = files
        self._tenant = tenant

    async def find(self, upload_id: str, kind: Kind) -> StoredObject:
        field = f"{kind}_upload"
        if not UPLOAD_ID.fullmatch(upload_id):
            raise ApiError(400, f"Field {field!r} must be an upload id from POST /v1/uploads.")
        key = f"{UPLOADS}/{self._tenant}/{upload_id}"
        stored = await _run(self._files.storage.head, key)
        if stored is None or _expired(stored):
            raise ApiError(
                400,
                f"Unknown upload {upload_id!r}. An upload is kept for "
                f"{self._files.ttl_hours} hours after it arrives.",
            )
        stored_kind = stored.metadata.get(KIND)
        if stored_kind != kind:
            other = "icon" if kind == "image" else "image"
            raise ApiError(
                400, f"Upload {upload_id!r} is an {other}, so send it as {other}_upload."
            )
        return stored

    async def read(self, upload_id: str, kind: Kind) -> bytes:
        stored = await self.find(upload_id, kind)
        data = await _run(self._files.storage.get, stored.key)
        if data is None:
            raise ApiError(400, f"Unknown upload {upload_id!r}.")
        return data


def configured_files() -> Files | None:
    """The files of the bucket the settings name, or None when no storage is configured."""
    config = storage_settings()
    if config is None:
        return None
    return Files(s3_storage(config), config.public_files_url, config.ttl_hours, config.secret_key)
