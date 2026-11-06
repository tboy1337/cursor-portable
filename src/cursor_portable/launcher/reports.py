"""Parse process and shortcut reports from the operating system."""

from __future__ import annotations

import csv
from io import StringIO

from cursor_portable.launcher.processes import ProcessInfo
from cursor_portable.launcher.shortcuts import Shortcut


def parse_shortcut_report(text: str) -> tuple[Shortcut, ...]:
    """Parse ``location|target`` lines."""
    items: list[Shortcut] = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        location, target = line.split("|", 1)
        if location.strip() and target.strip():
            items.append(Shortcut(location.strip(), target.strip()))
    return tuple(items)


def render_shortcut_report(items: tuple[Shortcut, ...]) -> str:
    """Serialize shortcuts for a snapshot file."""
    return "".join(f"{item.location}|{item.target}\n" for item in items)


def parse_tasklist_csv(text: str) -> tuple[ProcessInfo, ...]:
    """Parse Windows ``tasklist /fo csv`` output."""
    reader = csv.reader(StringIO(text))
    items: list[ProcessInfo] = []
    for row in reader:
        if len(row) < 2 or row[0].casefold() == "image name":
            continue
        pid_text = row[1].replace(",", "").strip()
        if not pid_text.isdigit():
            continue
        items.append(ProcessInfo(pid=int(pid_text), name=row[0].strip()))
    return tuple(items)


def parse_ps(text: str) -> tuple[ProcessInfo, ...]:
    """Parse ``pid name`` lines."""
    items: list[ProcessInfo] = []
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        items.append(ProcessInfo(pid=int(parts[0]), name=parts[1].strip()))
    return tuple(items)
