"""Creates an API key for a tenant and prints it with the hash that goes into the tenants file.
The service keeps only the hash, so the key is shown this once.

With --hash it prints the hash of a key that exists already, read from standard input, or asked
for without echo in a terminal: the way to move a key from API_KEYS into a tenants file.

Standard library only, so it runs without the virtualenv.

Usage: python3 scripts/new_api_key.py
       python3 scripts/new_api_key.py --hash
"""

import argparse
import getpass
import hashlib
import secrets
import sys

PREFIX = "cg_"


def key_hash(key: str) -> str:
    return f"sha256:{hashlib.sha256(key.encode('utf-8')).hexdigest()}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--hash", action="store_true", help="hash an existing key instead of creating one"
    )
    arguments = parser.parse_args()

    if arguments.hash:
        key = getpass.getpass("Key: ") if sys.stdin.isatty() else sys.stdin.readline()
        key = key.strip()
        if not key:
            sys.exit("No key given.")
        print(key_hash(key))
        return

    key = PREFIX + secrets.token_urlsafe(32)
    print(f"{'Key, shown only now:':<27}{key}")
    print(f"{'Hash for the tenants file:':<27}{key_hash(key)}")


if __name__ == "__main__":
    main()
