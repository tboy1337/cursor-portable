# Changelog

## [Unreleased]

- The wizard and pack script install the newest relocatable CPython 3.14 on the drive.
- A push to main publishes a source-only GitHub release when `[project].version` in `pyproject.toml` changed, or when that version still has no release. That file is the only version to edit. Clone the repository to use the release.
