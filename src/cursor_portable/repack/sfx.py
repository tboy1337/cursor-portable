"""7-Zip self-extractor bytes for Windows and a POSIX stub for the other systems."""

from __future__ import annotations

from cursor_portable.failure import Failure

_SFX_CONFIG = """;!@Install@!UTF-8!
Title="Cursor Portable"
GUIMode="2"
InstallPath="%TEMP%\\\\cursor-portable-setup"
OverwriteMode="2"
RunProgram="python\\\\python.exe -m cursor_portable"
;!@InstallEnd@!
"""

_POSIX = """#!/usr/bin/env bash
set -euo pipefail
EXTRACTOR_OFFSET={extractor_offset:012d}
EXTRACTOR_SIZE={extractor_size:012d}
ARCHIVE_OFFSET={archive_offset:012d}
ARCHIVE_SIZE={archive_size:012d}
SELF="$0"
TMP="$(mktemp -d)"
cleanup() {{ rm -rf "$TMP"; }}
trap cleanup EXIT
tail -c +$((EXTRACTOR_OFFSET + 1)) "$SELF" | head -c "$EXTRACTOR_SIZE" > "$TMP/7zz"
tail -c +$((ARCHIVE_OFFSET + 1)) "$SELF" | head -c "$ARCHIVE_SIZE" > "$TMP/setup.7z"
chmod +x "$TMP/7zz"
"$TMP/7zz" x "-o$TMP/root" -y "$TMP/setup.7z"
PY="$TMP/root/python/bin/python3"
exec "$PY" -m cursor_portable "$@"
"""


def windows_sfx_config() -> str:
    """Return the 7-Zip SFX config block."""
    return _SFX_CONFIG


def build_windows_sfx(sfx_module: bytes, archive: bytes) -> bytes:
    """Concatenate the SFX module, config, and ``.7z`` archive."""
    config = windows_sfx_config().encode("utf-8")
    return sfx_module + config + archive


def _posix_script(extractor_size: int, archive_size: int) -> bytes:
    prototype = _POSIX.format(
        extractor_offset=0,
        extractor_size=extractor_size,
        archive_offset=0,
        archive_size=archive_size,
    ).encode("utf-8")
    script_length = len(prototype)
    rendered = _POSIX.format(
        extractor_offset=script_length,
        extractor_size=extractor_size,
        archive_offset=script_length + extractor_size,
        archive_size=archive_size,
    ).encode("utf-8")
    if len(rendered) != script_length:
        raise RuntimeError("POSIX self-extractor script length changed.")
    return rendered


def build_posix_sfx(extractor: bytes, archive: bytes) -> bytes:
    """Build a bash stub with a bundled 7zz and an appended ``.7z`` archive."""
    if not extractor or not archive:
        raise RuntimeError("POSIX self-extractor needs an extractor and an archive.")
    script = _posix_script(len(extractor), len(archive))
    return script + extractor + archive


def posix_offsets(blob: bytes) -> tuple[int, int, int, int] | Failure:
    """Read the offsets stored in a POSIX self-extractor."""
    header = blob.split(b"\n", 8)
    values: dict[str, int] = {}
    for line in header:
        if b"=" not in line:
            continue
        key, raw = line.split(b"=", 1)
        name = key.decode("ascii", errors="ignore")
        if name.endswith("OFFSET") or name.endswith("SIZE"):
            try:
                values[name] = int(raw.strip())
            except ValueError:
                return Failure("sfx", f"Bad offset line: {name}")
    required = (
        "EXTRACTOR_OFFSET",
        "EXTRACTOR_SIZE",
        "ARCHIVE_OFFSET",
        "ARCHIVE_SIZE",
    )
    if any(name not in values for name in required):
        return Failure("sfx", "POSIX self-extractor is missing offsets.")
    return (
        values["EXTRACTOR_OFFSET"],
        values["EXTRACTOR_SIZE"],
        values["ARCHIVE_OFFSET"],
        values["ARCHIVE_SIZE"],
    )
