"""MD5 checks for package downloads and install media."""

import hashlib
from pathlib import Path


def calculate_hash(path: Path) -> str:
    hasher = hashlib.md5()
    with path.open("rb") as file:
        while chunk := file.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_hash(path: Path, expected: str) -> bool:
    """Case-insensitive check; return False if the path is missing."""
    return path.exists() and calculate_hash(path).lower() == expected.lower()
