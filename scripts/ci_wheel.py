#!/usr/bin/env python3
"""Confirm the built wheel matches the version selected for release."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from cursor_portable._version import RELEASE_VERSION
from cursor_portable.failure import Failure

LOGGER = logging.getLogger(__name__)
_WHEEL_PREFIX = "cursor_portable"


def _size(path: Path) -> int:
    """Return the wheel size in bytes."""
    return path.stat().st_size


def find_wheel(dist: Path, version: str) -> Path | Failure:
    """Return the one wheel in ``dist`` whose name is this version."""
    if RELEASE_VERSION.fullmatch(version) is None:
        return Failure("version", f"Refusing version {version}")
    if not dist.is_dir():
        return Failure("wheel", f"{dist} is not a directory")
    pattern = f"{_WHEEL_PREFIX}-{version}-*.whl"
    matches = sorted(path for path in dist.glob(pattern) if path.is_file())
    LOGGER.info("Found %s wheel(s) matching %s in %s", len(matches), pattern, dist)
    if len(matches) != 1:
        names = ", ".join(path.name for path in matches) or "none"
        return Failure("wheel", f"Expected one {pattern} in {dist}, found {names}")
    wheel = matches[0]
    try:
        size = _size(wheel)
    except OSError as exc:
        return Failure("wheel", f"Could not read {wheel.name}: {exc}")
    if size == 0:
        return Failure("wheel", f"{wheel.name} is empty")
    LOGGER.info("Release wheel is %s (%s bytes)", wheel.name, size)
    return wheel


def main(argv: list[str] | None = None) -> int:
    """CLI for the wheel check that runs before the PyPI upload."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)
    found = find_wheel(args.dist, args.version)
    if isinstance(found, Failure):
        LOGGER.error(found.message)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
