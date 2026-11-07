# Changelog

## [Unreleased]

- The wizard downloads a relocatable Git onto the drive and prepends it while Cursor is open. Windows uses MinGit. macOS and Linux use the relocatable Git builds from dugite-native.
- The setup wizard uses tkinter from the Python standard library.
- The wizard and pack script install the newest relocatable CPython 3.14 on the drive.
- A push to main publishes a source-only GitHub release and uploads the wheel to PyPI when `[project].version` in `pyproject.toml` changed, or when that version still has no release. That file is the only version to edit. Install the release with `pip install cursor-portable`.
