#!/usr/bin/env bash
set -euo pipefail
mkdir -p "${HOME:?}" /tmp/uv-cache
uv python install 3.13
uv sync --extra dev --frozen --no-editable --python 3.13
uv run --python 3.13 python scripts/docker_session.py
