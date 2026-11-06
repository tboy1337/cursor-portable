# Contributing

Install the dev tools with `uv sync --extra dev`. Run `uv run python scripts/verify.py` before a change is finished. The system Python cannot import this package or type-check the Qt wizard.

## Release

The version is `[project].version` in `pyproject.toml`. Do not copy it into another file. A push to `main` that changes that version, after CI passes, packs the setup for each system and publishes a GitHub release tagged with the new version. A push that does not change the version does not publish.
