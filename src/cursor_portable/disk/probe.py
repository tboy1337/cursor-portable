"""Parse volume listings. The parsers do not touch disks."""

from __future__ import annotations

import json
import logging
from typing import Mapping, cast

from cursor_portable.disk.volume import Volume
from cursor_portable.failure import Failure

LOGGER = logging.getLogger(__name__)

_PORTABLE_BUS_TYPES = frozenset({4, 7, 12, 13})
_PORTABLE_MEDIA = frozenset({"external hard disk media", "removable media"})


def _text(value: object, default: str = "") -> str:
    if isinstance(value, str):
        return value
    return default


def _int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.casefold() in {"1", "true", "yes"}
    return False


def _writable(item: Mapping[object, object]) -> bool:
    if "writable" not in item:
        return True
    return _bool(item.get("writable"))


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def windows_is_portable(
    *,
    drive_type: int,
    interface_type: str,
    bus_type: int | None,
    media_type: str,
) -> bool:
    """Return whether these Windows facts describe a disk that can be unplugged.

    A USB dock often reports the disk as fixed (drive type 3) with a SCSI
    interface. The storage bus and media type identify that disk: bus 4 is
    IEEE 1394, 7 is USB, 12 is SD, and 13 is MMC.
    """
    if drive_type == 2 or interface_type.casefold() == "usb":
        return True
    if bus_type in _PORTABLE_BUS_TYPES:
        return True
    return " ".join(media_type.casefold().split()) in _PORTABLE_MEDIA


def _removable(item: Mapping[object, object]) -> bool:
    if "drive_type" not in item:
        return _bool(item.get("removable"))
    drive_type = _int(item.get("drive_type"))
    interface_type = _text(item.get("interface_type"))
    bus_type = _optional_int(item.get("bus_type"))
    media_type = _text(item.get("media_type"))
    portable = windows_is_portable(
        drive_type=drive_type,
        interface_type=interface_type,
        bus_type=bus_type,
        media_type=media_type,
    )
    LOGGER.info(
        "Windows volume %s drive_type=%s interface=%s bus=%s media=%s portable=%s",
        _text(item.get("mount_point")),
        drive_type,
        interface_type,
        bus_type,
        media_type,
        portable,
    )
    return portable


def volumes_from_objects(records: object) -> tuple[Volume, ...] | Failure:
    """Build volumes from a list of objects produced by a platform probe."""
    if not isinstance(records, list):
        return Failure("volumes", "Volume probe did not return a list.")
    volumes: list[Volume] = []
    for record in cast(list[object], records):
        if not isinstance(record, dict):
            return Failure("volumes", "Volume probe record is not an object.")
        item = cast(Mapping[object, object], record)
        device = _text(item.get("device_id"))
        mount = _text(item.get("mount_point"))
        if not device or not mount:
            return Failure(
                "volumes", "Volume probe record is missing a device or mount."
            )
        volumes.append(
            Volume(
                device_id=device,
                mount_point=mount,
                label=_text(item.get("label")),
                filesystem=_text(item.get("filesystem")),
                size_bytes=_int(item.get("size_bytes")),
                removable=_removable(item),
                is_system=_bool(item.get("is_system")),
                is_boot=_bool(item.get("is_boot")),
                writable=_writable(item),
            )
        )
    return tuple(volumes)


def parse_volume_json(text: str) -> tuple[Volume, ...] | Failure:
    """Parse the JSON list printed by the volume probes."""
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError as exc:
        return Failure("volumes", f"Volume probe JSON is invalid: {exc}")
    if isinstance(parsed, dict):
        parsed = [parsed]
    return volumes_from_objects(parsed)
