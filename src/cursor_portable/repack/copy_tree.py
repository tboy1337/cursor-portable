"""Copy a tree, skipping uninstallers and unchanged files."""

from __future__ import annotations

import logging
from pathlib import Path
import shutil
import subprocess

from cursor_portable.constants import UNINSTALLER_NAMES
from cursor_portable.runtime import is_windows

LOGGER = logging.getLogger(__name__)
_BULK_FILE_COUNT = 32


def _same_file(source: Path, destination: Path) -> bool:
    if not destination.is_file():
        return False
    source_stat = source.stat()
    destination_stat = destination.stat()
    return source_stat.st_size == destination_stat.st_size and int(
        source_stat.st_mtime
    ) == int(destination_stat.st_mtime)


def _excluded(
    relative: Path,
    *,
    skip_names: frozenset[str],
    skip_dirs: frozenset[str],
    skip_root_files: frozenset[str],
) -> bool:
    parts = relative.parts
    if parts[-1] in skip_names:
        return True
    if len(parts) == 1 and parts[0] in skip_root_files:
        return True
    return any(part in skip_dirs for part in parts[:-1])


def _pending(
    source: Path,
    destination: Path,
    *,
    skip_names: frozenset[str],
    skip_dirs: frozenset[str],
    skip_root_files: frozenset[str],
) -> list[tuple[Path, Path]]:
    pending: list[tuple[Path, Path]] = []
    if not source.is_dir():
        return pending
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if _excluded(
            relative,
            skip_names=skip_names,
            skip_dirs=skip_dirs,
            skip_root_files=skip_root_files,
        ):
            LOGGER.info("Skipping %s", path.name)
            continue
        target = destination / relative
        if _same_file(path, target):
            continue
        pending.append((path, target))
    return pending


def _robocopy(
    source: Path,
    destination: Path,
    *,
    skip_names: frozenset[str],
    skip_dirs: frozenset[str],
    skip_root_files: frozenset[str],
) -> bool:
    command = [
        "robocopy",
        str(source),
        str(destination),
        "/E",
        "/MT:8",
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NP",
    ]
    excluded = [str(source / name) for name in sorted(skip_root_files)]
    excluded.extend(sorted(skip_names))
    if excluded:
        command.append("/XF")
        command.extend(excluded)
    if skip_dirs:
        command.append("/XD")
        command.extend(sorted(skip_dirs))
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        LOGGER.error("Could not start robocopy: %s", exc)
        return False
    if completed.returncode >= 8:
        detail = completed.stderr.strip() or completed.stdout.strip()
        LOGGER.error("robocopy failed: %s", detail)
        return False
    return True


def _tar_copy(
    source: Path,
    destination: Path,
    *,
    skip_names: frozenset[str],
    skip_dirs: frozenset[str],
    skip_root_files: frozenset[str],
) -> bool:
    command = ["tar", "-C", str(source), "-cf", "-", "."]
    for name in sorted(skip_names | skip_dirs):
        command.extend(["--exclude", name])
    for name in sorted(skip_root_files):
        command.extend(["--exclude", f"./{name}"])
    try:
        created = subprocess.Popen(command, stdout=subprocess.PIPE)
    except OSError as exc:
        LOGGER.error("Could not start tar: %s", exc)
        return False
    extracted = subprocess.run(
        ["tar", "-C", str(destination), "-xf", "-"],
        stdin=created.stdout,
        check=False,
        capture_output=True,
        text=True,
    )
    if created.stdout is not None:
        created.stdout.close()
    created.wait()
    if created.returncode != 0 or extracted.returncode != 0:
        detail = extracted.stderr.strip()
        LOGGER.error("tar copy failed: %s", detail)
        return False
    return True


def _copy_bulk(
    source: Path,
    destination: Path,
    pending_count: int,
    *,
    skip_names: frozenset[str],
    skip_dirs: frozenset[str],
    skip_root_files: frozenset[str],
) -> bool:
    if is_windows() and shutil.which("robocopy"):
        return _robocopy(
            source,
            destination,
            skip_names=skip_names,
            skip_dirs=skip_dirs,
            skip_root_files=skip_root_files,
        )
    if pending_count >= _BULK_FILE_COUNT and shutil.which("tar"):
        return _tar_copy(
            source,
            destination,
            skip_names=skip_names,
            skip_dirs=skip_dirs,
            skip_root_files=skip_root_files,
        )
    return False


def _copy_files(pending: list[tuple[Path, Path]]) -> None:
    for source, target in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def copy_tree(
    source: Path,
    destination: Path,
    *,
    skip_names: frozenset[str] = UNINSTALLER_NAMES,
    skip_dirs: frozenset[str] = frozenset(),
    skip_root_files: frozenset[str] = frozenset(),
) -> int:
    """Copy files from ``source`` to ``destination``. Return the number written."""
    pending = _pending(
        source,
        destination,
        skip_names=skip_names,
        skip_dirs=skip_dirs,
        skip_root_files=skip_root_files,
    )
    destination.mkdir(parents=True, exist_ok=True)
    if pending and not _copy_bulk(
        source,
        destination,
        len(pending),
        skip_names=skip_names,
        skip_dirs=skip_dirs,
        skip_root_files=skip_root_files,
    ):
        _copy_files(pending)
    LOGGER.info("Copied %s files from %s to %s", len(pending), source, destination)
    return len(pending)
