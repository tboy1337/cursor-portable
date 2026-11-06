# Changelog

## [Unreleased]

- The wizard and pack script install the newest relocatable CPython 3.14 on the drive.
- A push to main packs the setup and publishes a GitHub release when `[project].version` in `pyproject.toml` changed, or when that version still has no release. That file is the only version to edit.
- Windows packs take `7zSD.sfx` from the 7-Zip 24.09 LZMA SDK. An arm64 Windows pack installs the wizard's Qt wheels with the host tool when that runner cannot execute the packed interpreter.
