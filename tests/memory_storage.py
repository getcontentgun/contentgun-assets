from collections.abc import Iterator, Mapping
from pathlib import Path

from app.storage import StorageError, StoredObject


class MemoryStorage:
    """The storage interface over a dict, for the tests. With failing set, every call fails the
    way an unreachable bucket does."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, StoredObject]] = {}
        self.failing = False

    def _check(self) -> None:
        if self.failing:
            raise StorageError("The test storage is switched off")

    def location(self, key: str) -> str:
        return f"memory://test/{key}"

    def put(self, key: str, data: bytes, content_type: str, metadata: Mapping[str, str]) -> None:
        self._check()
        self.objects[key] = (
            bytes(data),
            StoredObject(key, len(data), content_type, dict(metadata)),
        )

    def head(self, key: str) -> StoredObject | None:
        self._check()
        found = self.objects.get(key)
        return None if found is None else found[1]

    def get(self, key: str) -> bytes | None:
        self._check()
        found = self.objects.get(key)
        return None if found is None else found[0]

    def open(self, key: str) -> tuple[StoredObject, Iterator[bytes]] | None:
        self._check()
        found = self.objects.get(key)
        if found is None:
            return None
        data, stored = found
        return stored, iter([data[:7], data[7:]])

    def keys(self, prefix: str) -> list[str]:
        self._check()
        return sorted(key for key in self.objects if key.startswith(prefix))

    def put_folder(self, prefix: str, folder: Path) -> None:
        """Every file below folder under prefix, the way bin/sync of the templates repository
        lays out a bundle."""
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                key = f"{prefix}/{path.relative_to(folder).as_posix()}"
                self.put(key, path.read_bytes(), "application/octet-stream", {})
