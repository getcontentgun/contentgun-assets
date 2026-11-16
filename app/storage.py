import logging
import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings

logger = logging.getLogger(__name__)

CHUNK_BYTES = 65_536
MISSING_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")


class StorageError(Exception):
    """The bucket could not be reached, or answered with an error other than a missing key."""


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    content_type: str
    metadata: Mapping[str, str] = field(default_factory=dict)


class Storage(Protocol):
    """A flat key space of objects. Every method blocks; asynchronous callers run it in a
    thread."""

    def location(self, key: str) -> str: ...

    def put(
        self, key: str, data: bytes, content_type: str, metadata: Mapping[str, str]
    ) -> None: ...

    def head(self, key: str) -> StoredObject | None: ...

    def get(self, key: str) -> bytes | None: ...

    def open(self, key: str) -> tuple[StoredObject, Iterator[bytes]] | None: ...

    def keys(self, prefix: str) -> list[str]: ...


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _start(key: str) -> str:
    """The first two parts of a key: the random part of a result's key is what its URL hands
    out, so it never goes into a log line or a message."""
    parts = key.split("/")
    return key if len(parts) <= 2 else "/".join(parts[:2]) + "/…"


class S3Storage:
    def __init__(self, endpoint: str, region: str, bucket: str, access_key: str, secret_key: str):
        self.bucket = bucket
        # Path-style addressing needs no DNS name per bucket, and checksums only where the API
        # demands them: S3-compatible stores reject the CRC32 headers boto3 adds by default.
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        )
        self.client = boto3.session.Session().client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=config,
        )

    def location(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def _failure(self, action: str, key: str, exc: Exception) -> StorageError:
        reason = _code(exc) if isinstance(exc, ClientError) else type(exc).__name__
        where = self.location(_start(key))
        logger.warning("%s %s failed: %s", action, where, reason)
        return StorageError(f"{action} {where} failed: {reason}")

    def put(self, key: str, data: bytes, content_type: str, metadata: Mapping[str, str]) -> None:
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                Metadata=dict(metadata),
            )
        except (BotoCoreError, ClientError) as exc:
            raise self._failure("Writing", key, exc) from None

    def head(self, key: str) -> StoredObject | None:
        try:
            answer = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _code(exc) in MISSING_CODES:
                return None
            raise self._failure("Reading", key, exc) from None
        except BotoCoreError as exc:
            raise self._failure("Reading", key, exc) from None
        return self._stored(key, answer)

    def get(self, key: str) -> bytes | None:
        opened = self.open(key)
        if opened is None:
            return None
        _, chunks = opened
        try:
            return b"".join(chunks)
        except (BotoCoreError, ClientError) as exc:
            raise self._failure("Reading", key, exc) from None

    def open(self, key: str) -> tuple[StoredObject, Iterator[bytes]] | None:
        try:
            answer = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _code(exc) in MISSING_CODES:
                return None
            raise self._failure("Reading", key, exc) from None
        except BotoCoreError as exc:
            raise self._failure("Reading", key, exc) from None
        body = answer["Body"]

        def chunks() -> Iterator[bytes]:
            try:
                yield from body.iter_chunks(CHUNK_BYTES)
            finally:
                body.close()

        return self._stored(key, answer), chunks()

    def keys(self, prefix: str) -> list[str]:
        found = []
        try:
            pages = self.client.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=prefix
            )
            for page in pages:
                found.extend(item["Key"] for item in page.get("Contents", []))
        except (BotoCoreError, ClientError) as exc:
            raise self._failure("Listing", prefix, exc) from None
        return found

    @staticmethod
    def _stored(key: str, answer: Mapping) -> StoredObject:
        return StoredObject(
            key=key,
            size=int(answer.get("ContentLength", 0)),
            content_type=answer.get("ContentType") or "application/octet-stream",
            metadata=dict(answer.get("Metadata") or {}),
        )

    def ensure_bucket(self) -> bool:
        """Creates the bucket unless it exists. True if it was created."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return False
        except ClientError as exc:
            if _code(exc) not in MISSING_CODES | {"NoSuchBucket"}:
                raise self._failure("Checking", "", exc) from None
        except BotoCoreError as exc:
            raise self._failure("Checking", "", exc) from None
        try:
            self.client.create_bucket(Bucket=self.bucket)
        except (BotoCoreError, ClientError) as exc:
            raise self._failure("Creating", "", exc) from None
        return True

    def expire(self, prefixes: list[str], days: int) -> None:
        """Sets a lifecycle rule per prefix that deletes its objects after the given days."""
        rules = [
            {
                "ID": f"contentgun-{prefix.strip('/')}",
                "Status": "Enabled",
                "Filter": {"Prefix": prefix},
                "Expiration": {"Days": days},
            }
            for prefix in prefixes
        ]
        try:
            self.client.put_bucket_lifecycle_configuration(
                Bucket=self.bucket, LifecycleConfiguration={"Rules": rules}
            )
        except (BotoCoreError, ClientError) as exc:
            raise self._failure("Setting the lifecycle rules of", "", exc) from None


@dataclass(frozen=True)
class StorageSettings:
    endpoint: str
    region: str
    bucket: str
    access_key: str
    secret_key: str
    public_files_url: str
    ttl_hours: int

    @property
    def lifecycle_days(self) -> int:
        """Whole days after which nothing stored can be valid any longer, with a day to spare."""
        return math.ceil(self.ttl_hours / 24) + 1


def _http_url(name: str, value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise RuntimeError(f"{name} must be an http:// or https:// URL, not {value!r}.")
    if parts.query or parts.fragment:
        raise RuntimeError(f"{name} must not carry a query or a fragment.")
    return value.rstrip("/")


def storage_settings() -> StorageSettings | None:
    """The bucket and the public URL from the settings, or None when none of them is set. Some
    but not all of them stop the start."""
    values = {
        "S3_ENDPOINT": settings.s3_endpoint.strip(),
        "S3_REGION": settings.s3_region.strip(),
        "S3_BUCKET": settings.s3_bucket.strip(),
        "S3_ACCESS_KEY": settings.s3_access_key.strip(),
        "S3_SECRET_KEY": settings.s3_secret_key.strip(),
        "PUBLIC_FILES_URL": settings.public_files_url.strip(),
    }
    missing = [name for name, value in values.items() if not value]
    if len(missing) == len(values):
        return None
    if missing:
        verb = "is" if len(missing) == 1 else "are"
        raise RuntimeError(
            f"Storage needs {', '.join(values)} together, but {', '.join(missing)} {verb} empty. "
            "Set all of them, or none to run without storage."
        )
    if not BUCKET_NAME.fullmatch(values["S3_BUCKET"]):
        raise RuntimeError(f"S3_BUCKET {values['S3_BUCKET']!r} is not a valid bucket name.")
    return StorageSettings(
        endpoint=_http_url("S3_ENDPOINT", values["S3_ENDPOINT"]),
        region=values["S3_REGION"],
        bucket=values["S3_BUCKET"],
        access_key=values["S3_ACCESS_KEY"],
        secret_key=values["S3_SECRET_KEY"],
        public_files_url=_http_url("PUBLIC_FILES_URL", values["PUBLIC_FILES_URL"]),
        ttl_hours=settings.storage_ttl_hours,
    )


def s3_storage(config: StorageSettings) -> S3Storage:
    return S3Storage(
        endpoint=config.endpoint,
        region=config.region,
        bucket=config.bucket,
        access_key=config.access_key,
        secret_key=config.secret_key,
    )
