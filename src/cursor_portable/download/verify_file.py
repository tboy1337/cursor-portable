"""Size and SHA-256 checks for downloaded files."""

from __future__ import annotations

import hashlib
from pathlib import Path

from cursor_portable.failure import Failure


def file_sha256(path: Path) -> str:
    """Return the lowercase SHA-256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def check_download(
    path: Path,
    *,
    minimum_bytes: int,
    expected_sha256: str | None,
) -> Failure | None:
    """Refuse a short file or a hash mismatch."""
    size = path.stat().st_size
    if size < minimum_bytes:
        return Failure(
            "too-small",
            f"{path.name} is {size} bytes; the minimum is {minimum_bytes}.",
        )
    if expected_sha256 is None:
        return None
    actual = file_sha256(path)
    if actual.casefold() != expected_sha256.casefold():
        return Failure(
            "hash-mismatch",
            f"{path.name} SHA-256 is {actual}, expected {expected_sha256}.",
        )
    return None
