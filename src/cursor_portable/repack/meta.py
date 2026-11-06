"""Version file stored on the removable drive."""

from __future__ import annotations

from pathlib import Path

from cursor_portable.failure import Failure
from cursor_portable.json_io import dump_object, load_object, string_field
from cursor_portable.models import InstallScope, ProductKind


def write_version_file(
    path: Path,
    *,
    cursor_version: str,
    commit: str,
    scope: InstallScope,
    product: ProductKind,
    target_os: str | None = None,
    target_arch: str | None = None,
    filesystem: str | None = None,
) -> None:
    """Write ``meta/version.json``. Omitted target fields keep the previous values."""
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_os, previous_arch, previous_fs = read_stick_identity(path)
    document: dict[str, object] = {
        "commit": commit,
        "cursor_version": cursor_version,
        "filesystem": previous_fs if filesystem is None else filesystem,
        "product": product.value,
        "scope": scope.value,
        "target_arch": previous_arch if target_arch is None else target_arch,
        "target_os": previous_os if target_os is None else target_os,
    }
    path.write_text(dump_object(document), encoding="utf-8")


def read_stick_identity(path: Path) -> tuple[str, str, str]:
    """Return target system, CPU, and filesystem recorded on a stick."""
    if not path.is_file():
        return "", "", ""
    loaded = load_object(path.read_text(encoding="utf-8"))
    if isinstance(loaded, Failure):
        return "", "", ""
    return (
        string_field(loaded, "target_os") or "",
        string_field(loaded, "target_arch") or "",
        string_field(loaded, "filesystem") or "",
    )


def read_version_fields(
    path: Path,
) -> tuple[str, str, InstallScope, ProductKind]:
    """Return version, commit, scope, and product recorded beside an app tree."""
    fallback = ("unknown", "", InstallScope.USER, ProductKind.IDE)
    if not path.is_file():
        return fallback
    loaded = load_object(path.read_text(encoding="utf-8"))
    if isinstance(loaded, Failure):
        return fallback
    version = string_field(loaded, "cursor_version") or "unknown"
    commit = string_field(loaded, "commit") or ""
    return version, commit, _scope(loaded), _product(loaded)


def _scope(document: dict[str, object]) -> InstallScope:
    text = string_field(document, "scope")
    if text is None:
        return InstallScope.USER
    try:
        return InstallScope(text)
    except ValueError:
        return InstallScope.USER


def _product(document: dict[str, object]) -> ProductKind:
    text = string_field(document, "product")
    if text is None:
        return ProductKind.IDE
    try:
        return ProductKind(text)
    except ValueError:
        return ProductKind.IDE


def read_cursor_version(path: Path) -> str | None:
    """Return the Cursor version recorded on the stick or in the cache."""
    if not path.is_file():
        return None
    loaded = load_object(path.read_text(encoding="utf-8"))
    if isinstance(loaded, Failure):
        return None
    return string_field(loaded, "cursor_version")
