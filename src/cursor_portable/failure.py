"""Expected failures returned to callers instead of being raised."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Failure:
    """A refusal the user can act on."""

    code: str
    message: str
