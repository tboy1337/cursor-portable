"""Package version.

The only edited copy is ``[project].version`` in ``pyproject.toml``.
Copies of this package that leave the repository get a ``VERSION`` stamp.
"""

from __future__ import annotations

import importlib.metadata
import logging
from pathlib import Path
import re
import tomllib

LOGGER = logging.getLogger(__name__)
PROJECT_NAME = "cursor-portable"
RELEASE_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_STAMP = "VERSION"


def read_project_fields(text: str) -> tuple[str | None, str | None]:
    """Return the project name and version from pyproject text.

    Invalid TOML raises ``ValueError``. A file without a project table
    returns ``(None, None)``.
    """
    try:
        document: object = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"pyproject.toml is invalid: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("pyproject.toml is not a table")
    project = document.get("project")
    if not isinstance(project, dict):
        return None, None
    name = project.get("name")
    version = project.get("version")
    project_name = name.strip() if isinstance(name, str) else ""
    project_version = version.strip() if isinstance(version, str) else ""
    return project_name or None, project_version or None


def read_stamp(package_dir: Path) -> str | None:
    """Return the version stamped beside a copied package."""
    path = package_dir / _STAMP
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    LOGGER.debug("Using stamped version %s from %s", text, path)
    return text


def nearest_project_version(start: Path) -> str | None:
    """Return this project's version from the nearest pyproject.toml."""
    for directory in (start, *start.parents):
        candidate = directory / "pyproject.toml"
        if not candidate.is_file():
            continue
        name, version = read_project_fields(candidate.read_text(encoding="utf-8"))
        if name == PROJECT_NAME and version:
            LOGGER.debug("Using version %s from %s", version, candidate)
            return version
        LOGGER.debug("Ignoring %s while resolving the package version", candidate)
        return None
    return None


def resolve_version(package_dir: Path | None = None) -> str:
    """Return the package version from a stamp, pyproject.toml, or metadata."""
    origin = Path(__file__).resolve().parent if package_dir is None else package_dir
    stamped = read_stamp(origin)
    if stamped:
        return stamped
    found = nearest_project_version(origin)
    if found:
        return found
    try:
        installed = importlib.metadata.version(PROJECT_NAME)
    except importlib.metadata.PackageNotFoundError:
        installed = ""
    if installed:
        LOGGER.debug("Using installed metadata version %s", installed)
        return installed
    raise RuntimeError(
        "cursor-portable version is not in a VERSION stamp, pyproject.toml, "
        "or package metadata."
    )


def write_version_stamp(package_dir: Path, version: str) -> None:
    """Write the version next to a copied package."""
    text = version.strip()
    if not text:
        raise ValueError("Refusing to stamp an empty version")
    path = package_dir / _STAMP
    path.write_text(f"{text}\n", encoding="utf-8")
    LOGGER.info("Stamped %s %s at %s", PROJECT_NAME, text, path)


__version__ = resolve_version()
