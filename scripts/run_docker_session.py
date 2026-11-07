#!/usr/bin/env python3
"""Build and run the Linux session container.

The container downloads portable CPython and edits shell startup files
inside the container. The Windows py launcher and the host uv environment
stay on the host. This command stays out of scripts/verify.py and CI.
"""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import sys

LOGGER = logging.getLogger("run_docker_session")
_IMAGE = "cursor-portable-session"
# Mirrored in tests/shell/run_docker_session.sh.
_SESSION = """#!/usr/bin/env bash
set -euo pipefail
mkdir -p "${HOME:?}" /tmp/uv-cache
uv python install 3.13
uv sync --extra dev --frozen --no-editable --python 3.13
uv run --python 3.13 python scripts/docker_session.py
"""


def main(argv: list[str] | None = None) -> int:
    """Build the image and run the session. Return the docker status."""
    del argv
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    root = Path(__file__).resolve().parent.parent
    LOGGER.info("Container session runs the in-module bash script")
    built = _run(_build_command(root))
    if built != 0:
        return built
    return _run(_container_command(root), _SESSION)


def _build_command(root: Path) -> list[str]:
    dockerfile = root / "docker" / "Dockerfile"
    return [
        "docker",
        "build",
        "-f",
        str(dockerfile),
        "-t",
        _IMAGE,
        str(dockerfile.parent),
    ]


def _container_command(root: Path) -> list[str]:
    return [
        "docker",
        "run",
        "--privileged",
        "--rm",
        "-v",
        f"{root.as_posix()}:/work:ro",
        "-w",
        "/work",
        "-e",
        "CURSOR_PORTABLE_CACHE=/tmp/cursor-portable-payload",
        "-e",
        "UV_PROJECT_ENVIRONMENT=/opt/cursor-portable-venv",
        "-e",
        "UV_CACHE_DIR=/tmp/uv-cache",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "HOME=/tmp/session-home",
        _IMAGE,
        "bash",
        "-s",
    ]


def _run(command: list[str], script: str | None = None) -> int:
    LOGGER.info("Running %s", " ".join(command))
    try:
        completed = subprocess.run(command, check=False, input=script, text=True)
    except OSError as exc:
        LOGGER.error("Could not run %s: %s", command[0], exc)
        return 1
    LOGGER.info("%s exited %s", command[0], completed.returncode)
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
