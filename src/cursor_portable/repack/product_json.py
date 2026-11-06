"""Edit the portable copy of product.json. The installed copy is never opened."""

from __future__ import annotations

import logging
from pathlib import Path
import re

from cursor_portable.failure import Failure
from cursor_portable.json_io import dump_object, load_object, string_field

LOGGER = logging.getLogger(__name__)

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def application_root(product: Path) -> Path | None:
    """Return the app directory that contains this ``product.json``.

    Windows and Linux keep it at ``resources/app/product.json``. macOS keeps it
    at ``Cursor.app/Contents/Resources/app/product.json``.
    """
    if product.parent.name != "app" or len(product.parents) < 3:
        return None
    resources = product.parent.parent
    if resources.name == "Resources" and len(product.parents) >= 4:
        return product.parents[3]
    if resources.name == "resources":
        return product.parents[2]
    return None


def find_product_json(root: Path) -> Path | None:
    """Find ``resources/app/product.json`` under ``root``."""
    matches = sorted(root.rglob("product.json"))
    for match in matches:
        if (
            match.parent.name == "app"
            and match.parent.parent.name.casefold() == "resources"
        ):
            return match
    return None


def strip_install_target(document: dict[str, object]) -> dict[str, object]:
    """Return a copy without the installer ``target`` property."""
    if "target" not in document:
        return dict(document)
    updated = dict(document)
    del updated["target"]
    return updated


def read_product_version(document: dict[str, object]) -> str:
    """Return the top-level semver, or ``unknown``."""
    value = string_field(document, "version")
    if value is None or _SEMVER.match(value) is None:
        return "unknown"
    return value


def read_commit(document: dict[str, object]) -> str:
    """Return the top-level commit, or an empty string."""
    value = string_field(document, "commit")
    if value is None:
        return ""
    return value


def strip_product_file(path: Path) -> Failure | None:
    """Remove ``target`` from a product.json file."""
    loaded = load_object(path.read_text(encoding="utf-8"))
    if isinstance(loaded, Failure):
        return loaded
    updated = strip_install_target(loaded)
    path.write_text(dump_object(updated), encoding="utf-8")
    LOGGER.info("Removed install target from %s", path)
    return None
