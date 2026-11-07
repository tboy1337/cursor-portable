"""7-Zip self-extractor bytes for Windows and a POSIX stub for the other systems."""

from __future__ import annotations

from cursor_portable.failure import Failure
from cursor_portable.shell import shell_text

_SFX_CONFIG = """;!@Install@!UTF-8!
Title="Cursor Portable"
GUIMode="2"
InstallPath="%TEMP%\\\\cursor-portable-setup"
OverwriteMode="2"
RunProgram="python\\\\python.exe -m cursor_portable"
;!@InstallEnd@!
"""

_OFFSET_NAMES = (
    "EXTRACTOR_OFFSET",
    "EXTRACTOR_SIZE",
    "ARCHIVE_OFFSET",
    "ARCHIVE_SIZE",
)
_OFFSET_LIMIT = 999_999_999_999


def windows_sfx_config() -> str:
    """Return the 7-Zip SFX config block."""
    return _SFX_CONFIG


def build_windows_sfx(sfx_module: bytes, archive: bytes) -> bytes:
    """Concatenate the SFX module, config, and ``.7z`` archive."""
    config = windows_sfx_config().encode("utf-8")
    return sfx_module + config + archive


def _posix_template() -> str:
    text = shell_text("posix-extractor.sh").replace("\r\n", "\n").replace("\r", "\n")
    if text.endswith("\n"):
        return text
    return f"{text}\n"


def _render_posix(template: str, values: dict[str, int]) -> str:
    """Fill the 12-digit offset fields without changing the script length."""
    rendered = template
    for name in _OFFSET_NAMES:
        value = values[name]
        if value < 0 or value > _OFFSET_LIMIT:
            raise RuntimeError(f"{name} does not fit in 12 digits.")
        token = f"{name}={0:012d}"
        if rendered.count(token) != 1:
            raise RuntimeError(f"POSIX extractor has no single {name} field.")
        rendered = rendered.replace(token, f"{name}={value:012d}", 1)
    return rendered


def _posix_script(extractor_size: int, archive_size: int) -> bytes:
    template = _posix_template()
    script_length = len(template.encode("utf-8"))
    rendered = _render_posix(
        template,
        {
            "EXTRACTOR_OFFSET": script_length,
            "EXTRACTOR_SIZE": extractor_size,
            "ARCHIVE_OFFSET": script_length + extractor_size,
            "ARCHIVE_SIZE": archive_size,
        },
    )
    encoded = rendered.encode("utf-8")
    if len(encoded) != script_length:
        raise RuntimeError("POSIX self-extractor script length changed.")
    return encoded


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
