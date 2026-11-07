"""Edit a PATH string by adding or removing one exact segment."""

from __future__ import annotations

from cursor_portable.constants import PATH_BLOCK_END, PATH_BLOCK_START
from cursor_portable.models import HostSystem


def separator_for(system: HostSystem) -> str:
    """Return the PATH separator for an operating system."""
    if system is HostSystem.WINDOWS:
        return ";"
    return ":"


def split_path(path: str, separator: str) -> list[str]:
    """Split PATH and drop empty segments."""
    return [part for part in path.split(separator) if part != ""]


def prepend_segments(path: str, segments: tuple[str, ...], separator: str) -> str:
    """Prepend missing segments so the first segment stays first."""
    updated = path
    for segment in reversed(segments):
        updated = prepend_segment(updated, segment, separator)
    return updated


def prepend_segment(path: str, segment: str, separator: str) -> str:
    """Prepend ``segment`` when it is not already present."""
    parts = split_path(path, separator)
    if segment in parts:
        return separator.join(parts)
    return separator.join([segment, *parts])


def remove_segment(path: str, segment: str, separator: str) -> str:
    """Remove every exact ``segment`` and keep the other entries in order."""
    parts = [part for part in split_path(path, separator) if part != segment]
    return separator.join(parts)


def remove_segments(path: str, segments: tuple[str, ...], separator: str) -> str:
    """Remove each exact segment."""
    updated = path
    for segment in segments:
        updated = remove_segment(updated, segment, separator)
    return updated


# Mirrored in tests/shell/prepend-path.sh.
_PREPEND = r"""cursor_portable_bin='__SEGMENT__'
case ":${PATH}:" in
    *":${cursor_portable_bin}:"*) ;;
    *) PATH="${cursor_portable_bin}:${PATH}"; export PATH ;;
esac
unset cursor_portable_bin
"""


def _assignment_template() -> str:
    text = _PREPEND.replace("\r\n", "\n").replace("\r", "\n")
    if text.endswith("\n"):
        return text
    return f"{text}\n"


def _shell_assignment(name: str, segment: str) -> str:
    quoted = segment.replace("'", "'\\''")
    rendered = _assignment_template().replace("cursor_portable_bin", name)
    return rendered.replace("__SEGMENT__", quoted)


def path_block(segment: str | tuple[str, ...]) -> str:
    """Return a shell block that prepends one or more directories."""
    segments = (segment,) if isinstance(segment, str) else segment
    body: list[str] = []
    for index, item in enumerate(segments):
        name = "cursor_portable_bin"
        if len(segments) != 1:
            name = f"cursor_portable_bin_{index}"
        body.append(_shell_assignment(name, item))
    return (
        f"{PATH_BLOCK_START}\n"
        "# Added while Cursor Portable is open. Removed when it exits.\n"
        + "".join(body)
        + f"{PATH_BLOCK_END}\n"
    )


def remove_path_block(text: str) -> str | None:
    """Remove a complete marked block. An unterminated block is left in place."""
    start = text.find(PATH_BLOCK_START)
    if start < 0:
        return text
    end = text.find(PATH_BLOCK_END, start)
    if end < 0:
        return None
    end += len(PATH_BLOCK_END)
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[:start] + text[end:]


def insert_path_block(text: str, segment: str | tuple[str, ...]) -> str:
    """Replace any existing block and append the current one."""
    stripped = remove_path_block(text)
    base = text if stripped is None else stripped
    if base and not base.endswith("\n"):
        base += "\n"
    return base + path_block(segment)
