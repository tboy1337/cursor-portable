"""A removable or fixed volume as the wizard sees it."""

from __future__ import annotations

from dataclasses import dataclass
import re

from cursor_portable.disk.filesystem import label_limit
from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:$")
_DEVICE = re.compile(r"^[A-Za-z0-9_./:-]+$")
_LABEL = re.compile(r"^[A-Za-z0-9 _.-]{0,32}$")


@dataclass(frozen=True)
class Volume:
    """One mounted filesystem."""

    device_id: str
    mount_point: str
    label: str
    filesystem: str
    size_bytes: int
    removable: bool
    is_system: bool
    is_boot: bool
    writable: bool = True


def validate_device(device: str, system: HostSystem) -> Failure | None:
    """Refuse a device id that could change the meaning of a command argument."""
    if system is HostSystem.WINDOWS:
        if _WINDOWS_DRIVE.match(device) is None:
            return Failure("bad-device", f"Not a drive letter: {device}")
        return None
    if ".." in device or _DEVICE.match(device) is None:
        return Failure("bad-device", f"Refusing device id: {device}")
    return None


def confirmation_token(volume: Volume) -> str:
    """Text the user must type before format or repair."""
    if volume.label.strip():
        return volume.label
    return volume.device_id


def confirmation_hint(volume: Volume) -> str:
    """Prompt shown beside the confirmation field."""
    if volume.label.strip():
        return "Type the volume label to confirm"
    return f"This drive has no label. Type {confirmation_token(volume)} to confirm."


def label_refusal(volume: Volume, untouched: str) -> str:
    """Explain a refused format or repair. The disk is left unchanged."""
    if volume.label.strip():
        return f"Type the volume label exactly. Nothing was {untouched}."
    token = confirmation_token(volume)
    return f"This drive has no label. Type {token} to confirm. Nothing was {untouched}."


def label_matches(volume: Volume, typed: str) -> bool:
    """Return whether the typed confirmation matches this volume."""
    if typed == confirmation_token(volume):
        return True
    if volume.label.strip():
        return False
    return _same_unlabeled_id(volume, typed)


def _windows_drive_letter(device_id: str) -> str | None:
    cleaned = device_id.strip().rstrip("\\/")
    if _WINDOWS_DRIVE.match(cleaned) is None:
        return None
    return cleaned[0]


def _unlabeled_ids(volume: Volume) -> frozenset[str]:
    values = [
        volume.device_id.strip().rstrip("\\/"),
        volume.mount_point.strip().rstrip("\\/"),
    ]
    letter = _windows_drive_letter(volume.device_id)
    if letter is None:
        return frozenset(item for item in values if item)
    values.append(letter)
    return frozenset(item.casefold() for item in values if item)


def _same_unlabeled_id(volume: Volume, typed: str) -> bool:
    candidate = typed.strip().rstrip("\\/")
    if not candidate:
        return False
    allowed = _unlabeled_ids(volume)
    if _windows_drive_letter(volume.device_id) is None:
        return candidate in allowed
    return candidate.casefold() in allowed


def safe_label(volume: Volume) -> str | Failure:
    """Return a label that can be passed as a format argument."""
    if _LABEL.match(volume.label) is None:
        return Failure("bad-label", "The volume label contains unsupported characters.")
    cleaned = "".join(ch for ch in volume.label if ch.isalnum() or ch in " _-").strip()
    if not cleaned:
        return Failure("bad-label", "The volume has no label that can be kept.")
    return cleaned[:11]


def volume_name(volume: Volume, filesystem: str) -> str:
    """Label to apply when formatting. Falls back to ``CURSOR``."""
    cleaned = "".join(ch for ch in volume.label if ch.isalnum() or ch in " _-").strip()
    if cleaned:
        return cleaned[: label_limit(filesystem)]
    return "CURSOR"
