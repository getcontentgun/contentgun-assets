"""Prepares the bucket that S3_BUCKET in .env names: creates it unless it exists, and sets the
lifecycle rules that delete outputs and uploads once they can no longer be valid. Run it once per
bucket, against the RustFS of compose.dev.yaml or the bucket of production.

Usage: .venv/bin/python scripts/setup_storage.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.files import OUTPUTS, UPLOADS  # noqa: E402
from app.storage import StorageError, s3_storage, storage_settings  # noqa: E402


def main() -> int:
    try:
        config = storage_settings()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    if config is None:
        print(
            "No storage is configured. Set S3_ENDPOINT, S3_REGION, S3_BUCKET, S3_ACCESS_KEY, "
            "S3_SECRET_KEY and PUBLIC_FILES_URL in .env, see docs/storage/README.md.",
            file=sys.stderr,
        )
        return 1

    storage = s3_storage(config)
    try:
        created = storage.ensure_bucket()
    except StorageError as error:
        print(f"{error}.", file=sys.stderr)
        return 1
    print(f"Bucket {storage.location('')} {'created' if created else 'exists already'}.")

    prefixes = [f"{OUTPUTS}/", f"{UPLOADS}/"]
    days = config.lifecycle_days
    try:
        storage.expire(prefixes, days)
    except StorageError as error:
        print(
            f"{error}. Set a rule that deletes {' and '.join(prefixes)} after {days} days in the "
            "console of the storage provider instead.",
            file=sys.stderr,
        )
        return 1
    print(f"Lifecycle rules set: {' and '.join(prefixes)} are deleted after {days} days.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
