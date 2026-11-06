#!/usr/bin/env python3
"""Publish packed Cursor Portable assets as one GitHub release."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import re
import subprocess

from cursor_portable._version import RELEASE_VERSION

LOGGER = logging.getLogger(__name__)
_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")


def checksum_lines(packs: Path) -> tuple[str, ...]:
    """Return the non-empty checksum files in name order."""
    lines: list[str] = []
    for path in sorted(packs.glob("*.sha256")):
        text = path.read_text(encoding="utf-8").strip()
        if text:
            lines.append(text)
    return tuple(lines)


def release_notes(version: str) -> str:
    """Return the release note body."""
    return (
        f"Cursor Portable {version}.\n\n"
        "This setup does not contain Cursor. "
        "Running it downloads the official Cursor build on this computer.\n"
        "A failed hash or size check fails the pack job and does not publish.\n"
    )


def release_command(
    packs: Path, version: str, target: str, sums: Path, notes: Path
) -> list[str]:
    """Return the gh release command for these assets."""
    assets = [str(path) for path in sorted(packs.iterdir()) if path.is_file()]
    return [
        "gh",
        "release",
        "create",
        version,
        *assets,
        str(sums),
        "--title",
        f"Cursor Portable {version}",
        "--notes-file",
        str(notes),
        "--target",
        target,
    ]


def _reject(version: str, target: str) -> str | None:
    if RELEASE_VERSION.fullmatch(version) is None:
        return f"Refusing version {version}"
    if _COMMIT.fullmatch(target) is None:
        return f"Refusing target {target}"
    return None


def publish_release(
    packs: Path, version: str, target: str, sums: Path, notes: Path
) -> int:
    """Write SHA256SUMS and notes, then create the GitHub release."""
    refused = _reject(version, target)
    if refused is not None:
        LOGGER.error(refused)
        return 1
    lines = checksum_lines(packs)
    if not lines:
        LOGGER.error("No checksum files in %s", packs)
        return 1
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    notes.write_text(release_notes(version), encoding="utf-8")
    command = release_command(packs, version, target, sums, notes)
    pack_files = [path for path in packs.iterdir() if path.is_file()]
    LOGGER.info("Publishing %s with %d pack files", version, len(pack_files))
    try:
        completed = subprocess.run(command, check=False, timeout=3600)
    except subprocess.TimeoutExpired:
        LOGGER.error(
            "gh release create timed out. A partial release may already exist."
        )
        return 1
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    """CLI for the version-bump publish job."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--packs", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--sums", type=Path, default=Path("SHA256SUMS"))
    parser.add_argument("--notes", type=Path, default=Path("notes.md"))
    args = parser.parse_args(argv)
    return publish_release(args.packs, args.version, args.target, args.sums, args.notes)


if __name__ == "__main__":
    raise SystemExit(main())
