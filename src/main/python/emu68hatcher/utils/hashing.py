"""MD5 and sha256 checks for downloads and install media."""

import hashlib
from pathlib import Path


def _file_digest(path: Path, algorithm: str) -> str:
    hasher = hashlib.new(algorithm)
    with path.open("rb") as file:
        while chunk := file.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def calculate_hash(path: Path) -> str:
    return _file_digest(path, "md5")


def calculate_sha256(path: Path) -> str:
    return _file_digest(path, "sha256")


def verify_hash(path: Path, expected: str) -> bool:
    """Case-insensitive check; return False if the path is missing."""
    return path.exists() and calculate_hash(path).lower() == expected.lower()
