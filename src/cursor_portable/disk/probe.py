"""Parse volume listings. The parsers do not touch disks."""

from __future__ import annotations

import json
from typing import Mapping, cast

from cursor_portable.disk.volume import Volume
from cursor_portable.failure import Failure


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
                removable=_bool(item.get("removable")),
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
