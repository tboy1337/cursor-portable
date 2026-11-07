"""Extract a Git archive into regular files.

Dugite archives use symlinks. The stick copy only carries regular files, and a
Windows host may not be allowed to create symlinks while building another
system's stick. Link members are written as copies of the target bytes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path, PurePosixPath
import stat
import tarfile
import zipfile

from cursor_portable.failure import Failure

LOGGER = logging.getLogger(__name__)
_LINK_LIMIT = 32


@dataclass(frozen=True)
class _Member:
    key: str
    kind: str
    link: str
    mode: int
    reader: Callable[[], bytes] | None


@dataclass(frozen=True)
class _FilePayload:
    data: bytes
    mode: int


def extract_git_archive(archive: Path, destination: Path) -> Failure | None:
    """Write ``archive`` into ``destination`` as a regular-file tree."""
    LOGGER.info("Extracting Git archive %s into %s", archive, destination)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as handle:
                return _write_opened(_zip_members(handle), destination)
        with tarfile.open(archive, "r:*") as handle:
            return _write_opened(_tar_members(handle), destination)
    except (
        OSError,
        tarfile.TarError,
        zipfile.BadZipFile,
        UnicodeError,
        ValueError,
    ) as exc:
        return Failure("git", f"Could not extract {archive.name}: {exc}")


def _write_opened(
    members: tuple[_Member, ...] | Failure,
    destination: Path,
) -> Failure | None:
    if isinstance(members, Failure):
        return members
    failure = _write_members(members, destination)
    if failure is not None:
        return failure
    LOGGER.info("Extracted %s Git members into %s", len(members), destination)
    return None


def relative_path(name: str) -> Path | None:
    """Return a relative path, or None when ``name`` escapes the archive."""
    text = name.replace("\\", "/")
    if text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
        return None
    parts: list[str] = []
    for part in PurePosixPath(text).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            return None
        parts.append(part)
    return Path(*parts)


def member_key(name: str) -> str | None:
    """Return the normalized archive key, or None when ``name`` is unsafe."""
    relative = relative_path(name)
    if relative is None:
        return None
    if not relative.parts:
        return ""
    return relative.as_posix()


def link_target_key(owner_key: str, linkname: str) -> str | None:
    """Resolve a symlink target relative to ``owner_key``."""
    text = linkname.replace("\\", "/")
    if text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
        return None
    parts = [
        part for part in PurePosixPath(owner_key).parent.parts if part not in {"", "."}
    ]
    for part in PurePosixPath(text).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _write_members(members: tuple[_Member, ...], destination: Path) -> Failure | None:
    by_key = {item.key: item for item in members if item.key}
    for item in members:
        failure = _write_member(item, by_key, destination)
        if failure is not None:
            return failure
    return None


def _write_member(
    item: _Member,
    by_key: dict[str, _Member],
    destination: Path,
) -> Failure | None:
    if not item.key:
        return None
    target = destination.joinpath(*PurePosixPath(item.key).parts)
    if item.kind == "dir":
        target.mkdir(parents=True, exist_ok=True)
        return None
    if item.kind == "file":
        return _store_member(item, target)
    if item.kind in {"symlink", "hardlink"}:
        return _store_link(item, by_key, target)
    return Failure("git", f"Refusing archive member {item.key}.")


def _store_member(item: _Member, target: Path) -> Failure | None:
    payload = _read_member(item)
    if isinstance(payload, Failure):
        return payload
    _store_file(target, payload, item.mode)
    return None


def _store_link(
    item: _Member,
    by_key: dict[str, _Member],
    target: Path,
) -> Failure | None:
    key = _next_key(item)
    if isinstance(key, Failure):
        return key
    resolved = _resolve_file(by_key, key)
    if isinstance(resolved, Failure):
        return resolved
    if resolved is None:
        target.mkdir(parents=True, exist_ok=True)
        return None
    _store_file(target, resolved.data, resolved.mode)
    return None


def _resolve_file(
    by_key: dict[str, _Member],
    key: str,
) -> _FilePayload | Failure | None:
    current = key
    seen: set[str] = set()
    for _step in range(_LINK_LIMIT):
        failure = _seen_member(current, seen, by_key)
        if isinstance(failure, Failure):
            return failure
        item = by_key[current]
        if item.kind == "dir":
            return None
        if item.kind == "file":
            return _file_payload(item)
        nxt = _next_key(item)
        if isinstance(nxt, Failure):
            return nxt
        current = nxt
    return Failure("git", "The Git archive link chain is too long.")


def _seen_member(
    current: str,
    seen: set[str],
    by_key: dict[str, _Member],
) -> Failure | None:
    if current in seen:
        return Failure("git", f"The Git archive link {current} is cyclic.")
    seen.add(current)
    if current not in by_key:
        return Failure("git", f"The Git archive link target {current} is missing.")
    return None


def _file_payload(item: _Member) -> _FilePayload | Failure:
    payload = _read_member(item)
    if isinstance(payload, Failure):
        return payload
    return _FilePayload(payload, item.mode)


def _next_key(item: _Member) -> str | Failure:
    if item.kind == "hardlink":
        target = member_key(item.link)
    else:
        target = link_target_key(item.key, item.link)
    if not target:
        return Failure("git", f"Refusing link {item.key}.")
    return target


def _read_member(item: _Member) -> bytes | Failure:
    if item.reader is None:
        return Failure("git", f"The Git archive member {item.key} has no data.")
    try:
        return item.reader()
    except OSError as exc:
        return Failure("git", f"Could not read {item.key}: {exc}")


def _store_file(target: Path, payload: bytes, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    if mode & 0o111:
        target.chmod(mode & 0o777)


def _tar_members(handle: tarfile.TarFile) -> tuple[_Member, ...] | Failure:
    items: list[_Member] = []
    for info in handle.getmembers():
        key = member_key(info.name)
        if key is None:
            return Failure("git", f"Refusing archive member {info.name}.")
        items.append(_tar_member(handle, info, key))
    return tuple(items)


def _tar_member(handle: tarfile.TarFile, info: tarfile.TarInfo, key: str) -> _Member:
    kind = _tar_kind(info)
    reader = _tar_reader(handle, info) if kind == "file" else None
    link = info.linkname if kind in {"symlink", "hardlink"} else ""
    return _Member(key=key, kind=kind, link=link, mode=info.mode, reader=reader)


def _tar_kind(info: tarfile.TarInfo) -> str:
    if info.isdir():
        return "dir"
    if info.isfile():
        return "file"
    if info.issym():
        return "symlink"
    if info.islnk():
        return "hardlink"
    return "other"


def _tar_reader(handle: tarfile.TarFile, info: tarfile.TarInfo) -> Callable[[], bytes]:
    def read() -> bytes:
        extracted = handle.extractfile(info)
        if extracted is None:
            raise OSError(f"Could not read {info.name}")
        return extracted.read()

    return read


def _zip_members(handle: zipfile.ZipFile) -> tuple[_Member, ...] | Failure:
    items: list[_Member] = []
    for info in handle.infolist():
        built = _zip_member(handle, info)
        if isinstance(built, Failure):
            return built
        items.append(built)
    return tuple(items)


def _zip_member(handle: zipfile.ZipFile, info: zipfile.ZipInfo) -> _Member | Failure:
    key = member_key(info.filename)
    if key is None:
        return Failure("git", f"Refusing archive member {info.filename}.")
    kind = _zip_kind(info)
    link = ""
    reader: Callable[[], bytes] | None = None
    if kind == "symlink":
        text = _zip_link_text(handle.read(info))
        if text is None:
            return Failure("git", f"Could not read link {info.filename}.")
        link = text
    elif kind == "file":
        reader = _zip_reader(handle, info)
    mode = (info.external_attr >> 16) & 0o777
    return _Member(key=key, kind=kind, link=link, mode=mode, reader=reader)


def _zip_kind(info: zipfile.ZipInfo) -> str:
    if info.is_dir():
        return "dir"
    if stat.S_ISLNK(info.external_attr >> 16):
        return "symlink"
    return "file"


def _zip_link_text(payload: bytes) -> str | None:
    text = payload.decode("utf-8", "surrogateescape").strip("\x00\r\n")
    if not text:
        return None
    return text


def _zip_reader(handle: zipfile.ZipFile, info: zipfile.ZipInfo) -> Callable[[], bytes]:
    def read() -> bytes:
        return handle.read(info)

    return read
