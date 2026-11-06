"""Logging for library code. Console output stays in the CLI."""

from __future__ import annotations

import logging
import sys

LOGGER = logging.getLogger("cursor_portable")
LOGGER.addHandler(logging.NullHandler())


class _CurrentStderrHandler(logging.Handler):
    """Write to the process stderr that is current at emit time."""

    def emit(self, record: logging.LogRecord) -> None:
        """Format one record onto stderr."""
        try:
            sys.stderr.write(self.format(record) + "\n")
            sys.stderr.flush()
        except (AttributeError, OSError, ValueError):
            # stderr can already be closed while the interpreter is shutting down.
            pass


def configure_cli_logging(level: int = logging.INFO) -> None:
    """Attach one stderr handler when the process has not configured logging."""
    configured = any(
        not isinstance(handler, logging.NullHandler) for handler in LOGGER.handlers
    )
    if configured:
        return
    LOGGER.handlers.clear()
    handler = _CurrentStderrHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(level)
    LOGGER.propagate = False
