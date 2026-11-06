"""Read Cursor's CLI install script as text and take the asset URL from it."""

from __future__ import annotations

import re

from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem

_URL = re.compile(
    r"https://(?:downloads\.cursor\.com|cursor\.com|api2\.cursor\.sh)/[^\s\"'`<>]+"
)
_LAB_PREFIX = re.compile(
    r"https://downloads\.cursor\.com/lab/[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9A-Fa-f]+/"
)
_WINDOWS_ZIP = re.compile(r"windows/\$architecture/agent-cli-package\.zip")
_ARCH = frozenset({"x64", "arm64"})
_CLI_OS = {
    HostSystem.LINUX: "linux",
    HostSystem.MACOS: "darwin",
    HostSystem.WINDOWS: "windows",
}


def extract_download_urls(script_text: str) -> tuple[str, ...]:
    """Return HTTPS download URLs found in an install script."""
    found: list[str] = []
    for match in _URL.finditer(script_text):
        url = match.group(0).rstrip(").,;")
        if url not in found:
            found.append(url)
    return tuple(found)


def select_cli_archive(script_text: str) -> str | Failure:
    """Pick the archive URL from the official install script text."""
    urls = extract_download_urls(script_text)
    if not urls:
        return Failure(
            "cli-url",
            "The CLI install script did not contain a downloads.cursor.com URL.",
        )
    for url in urls:
        lowered = url.casefold()
        if lowered.endswith((".tar.gz", ".zip", ".gz")):
            return url
    return urls[0]


def cli_archive_url(script_text: str, system: HostSystem, arch: str) -> str | Failure:
    """Return the CLI archive URL for one target system and CPU."""
    if arch not in _ARCH:
        return Failure("cli-url", f"No Cursor CLI build for {arch}.")
    templated = _substitute_cli_template(script_text, system, arch)
    if templated is not None:
        return templated
    windows = _windows_cli_url(script_text, system, arch)
    if windows is not None:
        return windows
    return _concrete_cli_url(script_text)


def _substitute_cli_template(
    script_text: str,
    system: HostSystem,
    arch: str,
) -> str | Failure | None:
    """Fill ``${OS}`` and ``${ARCH}`` in a shell download URL."""
    for url in extract_download_urls(script_text):
        if "${OS}" not in url or "${ARCH}" not in url:
            continue
        resolved = url.replace("${OS}", _CLI_OS[system]).replace("${ARCH}", arch)
        if "$" in resolved:
            return _placeholder_left()
        return resolved
    return None


def _windows_cli_url(
    script_text: str,
    system: HostSystem,
    arch: str,
) -> str | Failure | None:
    """Build the Windows zip URL from the prefix stored in the install script."""
    if _WINDOWS_ZIP.search(script_text) is None:
        return None
    if system is not HostSystem.WINDOWS:
        return Failure(
            "cli-url",
            "The Windows CLI install script was read for a different system.",
        )
    match = _LAB_PREFIX.search(script_text)
    if match is None:
        return Failure("cli-url", "The Windows CLI install script has no lab URL.")
    return f"{match.group(0)}windows/{arch}/agent-cli-package.zip"


def _concrete_cli_url(script_text: str) -> str | Failure:
    selected = select_cli_archive(script_text)
    if isinstance(selected, Failure):
        return selected
    if "$" in selected:
        return _placeholder_left()
    return selected


def _placeholder_left() -> Failure:
    return Failure(
        "cli-url",
        "The CLI download URL still has an unsubstituted placeholder.",
    )
