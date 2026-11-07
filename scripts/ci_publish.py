#!/usr/bin/env python3
"""Publish one source-only GitHub release for the current version."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import re
import subprocess

from cursor_portable._version import RELEASE_VERSION

LOGGER = logging.getLogger(__name__)
_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")


def release_notes(version: str) -> str:
    """Return the release note body."""
    return (
        f"cursor-portable {version}.\n\n"
        "`pip install cursor-portable` installs this version from PyPI. "
        "Clone the repository and install it with `uv sync` to work from source. "
        "This release does not include a setup program.\n"
    )


def release_command(version: str, target: str, notes: Path) -> list[str]:
    """Return the gh command for a source-only release."""
    return [
        "gh",
        "release",
        "create",
        version,
        "--title",
        f"cursor-portable {version}",
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


def publish_release(version: str, target: str, notes: Path) -> int:
    """Create a GitHub release that contains only the tagged source."""
    refused = _reject(version, target)
    if refused is not None:
        LOGGER.error(refused)
        return 1
    notes.write_text(release_notes(version), encoding="utf-8")
    command = release_command(version, target, notes)
    LOGGER.info("Publishing source release %s at %s", version, target)
    try:
        completed = subprocess.run(command, check=False, timeout=120)
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
    parser.add_argument("--version", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--notes", type=Path, default=Path("notes.md"))
    args = parser.parse_args(argv)
    return publish_release(args.version, args.target, args.notes)


if __name__ == "__main__":
    raise SystemExit(main())
