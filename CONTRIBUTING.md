# Contributing

Install the dev tools with `uv sync --extra dev`. Run `uv run python scripts/verify.py` before a change is finished. The system Python cannot import this package or type-check the Qt wizard.

## Release

The version is `[project].version` in `pyproject.toml`. Do not copy it into another file. A push to `main`, after CI passes, packs the setup for each system and publishes a GitHub release when that version changed or when it still has no release. A push whose version already has a release does not publish.
