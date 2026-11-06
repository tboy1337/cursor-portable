"""Parse a SHA256SUMS file."""

from __future__ import annotations


def expected_sha256(text: str, file_name: str) -> str | None:
    """Return the digest listed for ``file_name``."""
    for line in text.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        parts = trimmed.split()
        if len(parts) < 2:
            continue
        listed = parts[-1].lstrip("*")
        if listed == file_name:
            return parts[0].casefold()
    return None


def render_sha256sums(entries: tuple[tuple[str, str], ...]) -> str:
    """Render ``SHA256SUMS`` lines as ``digest  filename``."""
    lines = [f"{digest}  {name}" for digest, name in entries]
    return "\n".join(lines) + ("\n" if lines else "")
