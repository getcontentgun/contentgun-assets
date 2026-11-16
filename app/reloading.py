import asyncio
import logging
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI

from app.errors import ApiError
from app.files import UNAVAILABLE, Files
from app.storage import Storage, StorageError
from app.tenants import TenantDirectory, TenantSource, configured_source

logger = logging.getLogger(__name__)

TEMPLATES = "templates/"
DOWNLOAD_THREADS = 8
UNSAFE_PARTS = frozenset({"", ".", ".."})


def _copy_plan(storage: Storage, target: Path) -> list[tuple[str, Path]]:
    plan = []
    for key in storage.keys(TEMPLATES):
        relative = key[len(TEMPLATES) :]
        # A key ending in a slash is the folder marker some upload tools write; it holds nothing.
        if not relative or relative.endswith("/"):
            continue
        parts = relative.split("/")
        path = target.joinpath(*parts)
        if any(
            part in UNSAFE_PARTS or "\\" in part or "\x00" in part for part in parts
        ) or not path.resolve().is_relative_to(target):
            raise RuntimeError(
                f"{storage.location(key)}: This key is no plain path below {TEMPLATES}, so it "
                "cannot be copied."
            )
        plan.append((key, path))
    return plan


def download_bundles(storage: Storage, target: Path) -> int:
    """Copies every object below templates/ in the bucket into target, an empty folder given as
    a resolved path, and returns how many files it copied."""
    plan = _copy_plan(storage, target)

    def copy(item: tuple[str, Path]) -> None:
        key, path = item
        data = storage.get(key)
        if data is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            raise RuntimeError(
                f"{storage.location(key)}: This file cannot be copied: {exc.strerror or exc}."
            ) from None

    with ThreadPoolExecutor(max_workers=DOWNLOAD_THREADS) as pool:
        list(pool.map(copy, plan))
    return len(plan)


class BundleCache:
    """Local copies of the tenant bundles in the bucket. A reload copies into a new folder and
    keeps the one before it, which requests that started earlier may still read from."""

    def __init__(self, storage: Storage, root: Path | None = None):
        self._storage = storage
        if root is None:
            root = Path(tempfile.mkdtemp(prefix="contentgun-bundles-"))
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve()
        self._kept: list[Path] = []

    @property
    def origin(self) -> str:
        return self._storage.location(TEMPLATES)

    def fill(self) -> Path:
        folder = Path(tempfile.mkdtemp(dir=self._root)).resolve()
        try:
            count = download_bundles(self._storage, folder)
        except BaseException:
            self.discard(folder)
            raise
        logger.info("Copied %d files of tenant bundles from %s", count, self.origin)
        return folder

    def keep(self, folder: Path) -> None:
        self._kept.append(folder)
        while len(self._kept) > 2:
            self.discard(self._kept.pop(0))

    def discard(self, folder: Path) -> None:
        shutil.rmtree(folder, ignore_errors=True)

    def in_bucket(self, message: str, folder: Path) -> str:
        """The message with the local copy's paths turned back into the bucket's."""
        return message.replace(f"{folder}/", self.origin).replace(str(folder), self.origin)

    def close(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)


class Reloader:
    """Loads the tenants and their bundles, at startup and again for POST /v1/admin/reload: the
    tenants file and BUNDLES_DIR as they are on disk, or the tenant bundles from the bucket."""

    def __init__(self, source: TenantSource, cache: BundleCache | None = None):
        self.source = source
        self._cache = cache
        self._lock = asyncio.Lock()

    @property
    def origin(self) -> str | None:
        """Where the bundles of the tenants come from: the bucket, a folder, or nowhere."""
        if self._cache is not None:
            return self._cache.origin
        return None if self.source.bundles_dir is None else str(self.source.bundles_dir)

    def load(self) -> TenantDirectory:
        if self._cache is None:
            return self.source.load()
        folder = self._cache.fill()
        try:
            directory = replace(self.source, bundles_dir=folder).load()
        except RuntimeError as exc:
            self._cache.discard(folder)
            raise RuntimeError(self._cache.in_bucket(str(exc), folder)) from None
        except BaseException:
            self._cache.discard(folder)
            raise
        self._cache.keep(folder)
        return directory

    def start(self) -> TenantDirectory:
        try:
            return self.load()
        except StorageError as exc:
            self.close()
            raise RuntimeError(f"The tenant bundles could not be copied: {exc}.") from None
        except BaseException:
            self.close()
            raise

    async def reload(self, app: FastAPI) -> TenantDirectory:
        async with self._lock:
            try:
                directory = await asyncio.to_thread(self.load)
            except StorageError:
                raise ApiError(503, UNAVAILABLE) from None
            except RuntimeError as exc:
                raise ApiError(
                    409, f"Nothing was reloaded, the tenants and bundles in use stay active. {exc}"
                ) from None
            app.state.tenants = directory
        logger.info("Reloaded tenants: %s", ", ".join(directory.names))
        return directory

    def close(self) -> None:
        if self._cache is not None:
            self._cache.close()


def service_reloader(files: Files | None) -> Reloader:
    """The reloader of the settings: with storage and without BUNDLES_DIR, the bundles of the
    tenants come from the bucket."""
    source = configured_source()
    cache = None
    if files is not None and source.bundles_dir is None:
        cache = BundleCache(files.storage)
    return Reloader(source, cache)
