"""JSON helpers that stay inside strict typing."""

from __future__ import annotations

import json
from typing import Mapping, Sequence, cast

from cursor_portable.failure import Failure


def load_object(text: str) -> dict[str, object] | Failure:
    """Parse a JSON object. Arrays and scalars are refused."""
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError as exc:
        return Failure("json", f"Could not parse JSON: {exc}")
    if not isinstance(parsed, dict):
        return Failure("json", "JSON root is not an object.")
    result: dict[str, object] = {}
    for key, value in cast(Mapping[object, object], parsed).items():
        if not isinstance(key, str):
            return Failure("json", "JSON object key is not a string.")
        result[key] = value
    return result


def dump_object(value: Mapping[str, object]) -> str:
    """Serialize an object with a trailing newline."""
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def string_field(document: Mapping[str, object], key: str) -> str | None:
    """Return a string field, or None when it is missing or not a string."""
    value = document.get(key)
    if isinstance(value, str):
        return value
    return None


def as_object_list(value: object) -> Sequence[object] | Failure:
    """Return a JSON array."""
    if isinstance(value, list):
        return cast(Sequence[object], value)
    return Failure("json", "Expected a JSON array.")
