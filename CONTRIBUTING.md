# Contributing

Install the dev tools with `uv sync --extra dev`. Run `uv run python scripts/verify.py` before a change is finished. The system Python cannot import this package. Wizard tests open a tkinter window, so Linux runs them under Xvfb.

## Release

The version is `[project].version` in `pyproject.toml`. Do not copy it into another file. A push to `main`, after CI passes, publishes a source-only GitHub release and uploads the wheel to PyPI when that version changed or when it still has no release. The upload uses trusted publishing in the `pypi` environment. `pip install cursor-portable` installs the release. A push whose version already has a release does not publish.
