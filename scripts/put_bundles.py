"""Copies the bundles of one tenant into the bucket that S3_BUCKET in .env names: every file below
the given folder goes to templates/<tenant>/ in the bucket, with the folder's name as the tenant.
Files already in the bucket are overwritten, nothing is deleted. POST /v1/admin/reload makes the
service pick them up.

Usage: .venv/bin/python scripts/put_bundles.py <folder of a tenant>
       .venv/bin/python scripts/put_bundles.py /path/to/tenants/acme
"""

import mimetypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.reloading import TEMPLATES  # noqa: E402
from app.storage import StorageError, s3_storage, storage_settings  # noqa: E402
from app.templating import NAME  # noqa: E402

USAGE = "Usage: .venv/bin/python scripts/put_bundles.py <folder of a tenant>"


def main(arguments: list[str]) -> int:
    if len(arguments) != 1:
        print(USAGE, file=sys.stderr)
        return 1
    folder = Path(arguments[0]).expanduser().resolve()
    if not folder.is_dir() or not NAME.fullmatch(folder.name):
        print(f"{folder} is no folder of a tenant, named like one.", file=sys.stderr)
        return 1
    try:
        config = storage_settings()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    if config is None:
        print("No storage is configured, see docs/storage/README.md.", file=sys.stderr)
        return 1

    storage = s3_storage(config)
    files = [
        path
        for path in sorted(folder.rglob("*"))
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(folder).parts)
    ]
    try:
        for path in files:
            key = f"{TEMPLATES}{folder.name}/{path.relative_to(folder).as_posix()}"
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            storage.put(key, path.read_bytes(), content_type, {})
    except StorageError as error:
        print(f"{error}.", file=sys.stderr)
        return 1
    print(f"Copied {len(files)} files to {storage.location(f'{TEMPLATES}{folder.name}/')}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
