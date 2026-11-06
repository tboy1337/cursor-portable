"""Line-oriented session file shared with the uninstall scripts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionRecord:
    """Host changes that must be undone when Cursor exits."""

    path_separator: str = ";"
    path_segments: list[str] = field(default_factory=list)
    path_kind: str = "plain"
    home_link: str = ""
    home_backup: str = ""
    home_target: str = ""
    cache_dir: str = ""
    stick_root: str = ""
    pid: str = ""
    shortcut_snapshot: str = ""

    def render(self) -> str:
        """Serialize the session as ``key=value`` lines."""
        lines = [
            f"path_separator={self.path_separator}",
            f"path_kind={self.path_kind}",
            f"home_link={self.home_link}",
            f"home_backup={self.home_backup}",
            f"home_target={self.home_target}",
            f"cache_dir={self.cache_dir}",
            f"stick_root={self.stick_root}",
            f"pid={self.pid}",
            f"shortcut_snapshot={self.shortcut_snapshot}",
        ]
        lines.extend(f"path_segment={segment}" for segment in self.path_segments)
        return "\n".join(lines) + "\n"


def parse_session(text: str) -> SessionRecord:
    """Parse a session file. Unknown keys are ignored."""
    record = SessionRecord()
    for line in text.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
            continue
        key, value = trimmed.split("=", 1)
        if key == "path_segment":
            record.path_segments.append(value)
        elif key == "path_separator":
            record.path_separator = value
        elif key == "path_kind":
            record.path_kind = value
        elif key == "home_link":
            record.home_link = value
        elif key == "home_backup":
            record.home_backup = value
        elif key == "home_target":
            record.home_target = value
        elif key == "cache_dir":
            record.cache_dir = value
        elif key == "stick_root":
            record.stick_root = value
        elif key == "pid":
            record.pid = value
        elif key == "shortcut_snapshot":
            record.shortcut_snapshot = value
    return record


def read_session(path: Path) -> SessionRecord | None:
    """Return the session file, or None when it is absent."""
    if not path.is_file():
        return None
    return parse_session(path.read_text(encoding="utf-8"))


def write_session(path: Path, record: SessionRecord) -> None:
    """Write the session file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.render(), encoding="utf-8")
