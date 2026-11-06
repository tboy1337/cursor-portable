"""Wizard steps with no widgets."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
import logging
from pathlib import Path

from cursor_portable.disk.filesystem import (
    display_filesystem,
    filesystem_for,
    filesystems_match,
    format_failure,
    host_can_format,
    host_can_write,
    prepare_failure,
)
from cursor_portable.disk.guards import guard_volume
from cursor_portable.disk.volume import Volume, confirmation_hint
from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem, InstallScope, ProductKind, ProfileMode

LOGGER = logging.getLogger(__name__)


class WizardStep(Enum):
    """One page of the wizard."""

    PRODUCT = "product"
    TARGET = "target"
    SCOPE = "scope"
    PROFILE = "profile"
    DRIVE = "drive"
    CONFIRM = "confirm"


class DriveDisposition(Enum):
    """Whether the selected drive can be used as it is."""

    READY = "ready"
    NEEDS_FORMAT = "needs-format"


def step_sequence(
    host: HostSystem,
    target: HostSystem | None = None,
) -> tuple[WizardStep, ...]:
    """Return the pages to show for this computer and the chosen target."""
    chosen = host if target is None else target
    pages = [WizardStep.PRODUCT, WizardStep.TARGET]
    if chosen is HostSystem.WINDOWS:
        pages.append(WizardStep.SCOPE)
    if chosen is host:
        pages.append(WizardStep.PROFILE)
    pages.extend((WizardStep.DRIVE, WizardStep.CONFIRM))
    return tuple(pages)


def next_step(
    host: HostSystem,
    current: WizardStep,
    target: HostSystem | None = None,
) -> WizardStep | None:
    """Return the page after ``current``."""
    pages = step_sequence(host, target)
    try:
        index = pages.index(current)
    except ValueError:
        return pages[0]
    if index + 1 >= len(pages):
        return None
    return pages[index + 1]


def default_scope(system: HostSystem) -> InstallScope:
    """macOS and Linux have no system installer. The recorded scope is user."""
    del system
    return InstallScope.USER


def classify_drive(
    volume: Volume,
    host: HostSystem,
    target: HostSystem | None = None,
    *,
    busy: bool,
    available_tools: frozenset[str] | None = None,
) -> DriveDisposition | Failure:
    """Accept the target filesystem, or ask to format when this computer can."""
    chosen = host if target is None else target
    failure = guard_volume(volume, host, busy=busy)
    if failure is not None:
        return failure
    expected = filesystem_for(chosen)
    if not host_can_write(host, expected):
        LOGGER.info(
            "Host %s cannot write %s for %s",
            host.value,
            expected,
            volume.mount_point,
        )
        return prepare_failure(volume.mount_point, expected)
    if filesystems_match(volume.filesystem, expected):
        return _ready(volume)
    if host_can_format(host, expected, available_tools=available_tools):
        return DriveDisposition.NEEDS_FORMAT
    LOGGER.info("Host %s cannot format %s", host.value, expected)
    return format_failure(host, expected)


def preferred_drive_index(
    volumes: tuple[Volume, ...],
    host: HostSystem,
    target: HostSystem,
    busy: Callable[[Volume], bool],
) -> int | None:
    """Return the only usable drive so the wizard can select it."""
    chosen: int | None = None
    for index, volume in enumerate(volumes):
        result = classify_drive(volume, host, target, busy=busy(volume))
        if not isinstance(result, DriveDisposition):
            continue
        if chosen is not None:
            return None
        chosen = index
    return chosen


def _ready(volume: Volume) -> DriveDisposition | Failure:
    if volume.writable:
        return DriveDisposition.READY
    return Failure(
        "read-only",
        f"{volume.mount_point} is mounted read-only.",
    )


def drive_message(
    volume: Volume,
    disposition: DriveDisposition,
    target: HostSystem,
) -> str:
    """Text shown for the selected drive."""
    expected = display_filesystem(filesystem_for(target))
    hint = confirmation_hint(volume)
    if disposition is DriveDisposition.NEEDS_FORMAT:
        actual = volume.filesystem or "unknown"
        return (
            f"{volume.mount_point} is {actual}. "
            f"Format it as {expected} to continue. This erases the drive. {hint}"
        )
    return (
        f"{volume.mount_point} is {expected}, {volume.size_bytes} bytes, "
        f"label {volume.label!r}. {hint}"
    )


def completion_lines(mount_point: str, target: HostSystem) -> tuple[str, ...]:
    """Text shown after a successful install, until the user closes the wizard."""
    root = Path(mount_point) / "CursorPortable"
    name = "CursorPortable.cmd" if target is HostSystem.WINDOWS else "cursor-portable"
    launcher = root / name
    return (
        "Cursor Portable is ready.",
        f"It is on {root}.",
        f"Start it with {launcher}.",
        "Click Close when you have read this.",
    )


def summary_lines(
    *,
    product: ProductKind,
    scope: InstallScope,
    profile: ProfileMode,
    volume: Volume,
    disposition: DriveDisposition,
    target: HostSystem,
    arch: str,
) -> tuple[str, ...]:
    """Text shown before files are copied."""
    expected = display_filesystem(filesystem_for(target))
    format_line = (
        f"Format {volume.mount_point} as {expected} before copying."
        if disposition is DriveDisposition.NEEDS_FORMAT
        else f"Keep the {expected} filesystem on {volume.mount_point}."
    )
    return (
        f"Product: {product.value}",
        f"Target: {target.value} {arch}",
        f"Build: {scope.value}",
        f"Profile: {profile.value}",
        f"Drive: {volume.mount_point} ({volume.filesystem or 'unknown'})",
        format_line,
    )
