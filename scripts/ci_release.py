#!/usr/bin/env python3
"""Decide whether a push to main should publish a GitHub release.

A release is published when ``[project].version`` in ``pyproject.toml``
changes, and when that version still has no GitHub release. That file is
the only version to edit.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import subprocess

from cursor_portable._version import RELEASE_VERSION, read_project_fields
from cursor_portable.failure import Failure

LOGGER = logging.getLogger(__name__)
ZERO_SHA = "0" * 40
_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class ReleaseDecision:
    """Whether this push publishes, and the version to tag."""

    publish: bool
    version: str
    message: str


def choose_release(
    previous: str | None,
    current: str,
    *,
    new_history: bool,
    already_released: bool = True,
) -> ReleaseDecision | Failure:
    """Return a publish decision for the previous and current versions."""
    if not current:
        return Failure("version", "pyproject.toml has no project version")
    if new_history:
        LOGGER.info("Skipping release; this push has no previous commit")
        return ReleaseDecision(False, current, "no previous commit")
    if previous == current and already_released:
        LOGGER.info("Skipping release; version %s is unchanged", current)
        return ReleaseDecision(False, current, "unchanged")
    if RELEASE_VERSION.fullmatch(current) is None:
        return Failure("version", f"Refusing version {current}")
    if previous == current:
        LOGGER.info("Releasing %s; GitHub has no release for it", current)
        return ReleaseDecision(True, current, "unreleased")
    LOGGER.info("Releasing %s; previous version was %s", current, previous)
    return ReleaseDecision(True, current, "changed")


def github_release_exists(version: str) -> bool | Failure:
    """Return whether GitHub already has a release tagged ``version``."""
    LOGGER.info("Checking GitHub release %s", version)
    command = ["gh", "release", "view", version, "--json", "tagName"]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return Failure("release", f"gh release view timed out for {version}")
    except OSError as exc:
        return Failure("release", f"Could not run gh: {exc}")
    if completed.returncode == 0:
        LOGGER.info("GitHub release %s already exists", version)
        return True
    detail = "\n".join(
        part.strip() for part in (completed.stderr, completed.stdout) if part.strip()
    )
    if "release not found" in detail.casefold():
        LOGGER.info("GitHub release %s does not exist", version)
        return False
    LOGGER.error("gh release view failed: %s", detail)
    return Failure("release", detail or f"gh release view failed for {version}")


def version_in_text(text: str, label: str) -> str | Failure:
    """Return the cursor-portable version in one pyproject document."""
    try:
        name, version = read_project_fields(text)
    except ValueError as exc:
        return Failure("version", f"{label}: {exc}")
    if name != "cursor-portable" or version is None:
        return Failure(
            "version", f"{label} pyproject.toml has no cursor-portable version"
        )
    LOGGER.info("%s version is %s", label, version)
    return version


def missing_pyproject(stderr: str) -> bool:
    """Return whether git could not find pyproject.toml in that commit."""
    lowered = stderr.casefold()
    return "does not exist in" in lowered or "exists on disk, but not in" in lowered


def show_pyproject(sha: str, *, cwd: Path | None = None) -> str | Failure | None:
    """Return pyproject.toml at ``sha``, None when the commit lacks the file."""
    LOGGER.info("Reading pyproject.toml at %s", sha)
    try:
        completed = subprocess.run(
            ["git", "show", f"{sha}:pyproject.toml"],
            check=False,
            capture_output=True,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return Failure("git", f"git show timed out for {sha}")
    except OSError as exc:
        return Failure("git", f"Could not run git: {exc}")
    if completed.returncode == 0:
        return completed.stdout
    stderr = completed.stderr.strip()
    if missing_pyproject(stderr):
        LOGGER.info("Commit %s has no pyproject.toml", sha)
        return None
    LOGGER.error("git show %s failed: %s", sha, stderr)
    return Failure("git", stderr or f"git show failed for {sha}")


def write_github_output(path: Path, decision: ReleaseDecision) -> None:
    """Append the release decision for a GitHub Actions step."""
    flag = "true" if decision.publish else "false"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"release={flag}\nversion={decision.version}\n")
    LOGGER.info("Wrote release=%s version=%s", flag, decision.version)


def _current_version(path: Path) -> str | Failure:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Failure("version", f"Could not read {path}: {exc}")
    return version_in_text(text, "current")


def _previous_version(before: str) -> tuple[bool, str | None] | Failure:
    """Return ``(new_history, version)`` for the commit before this push."""
    if before == ZERO_SHA:
        LOGGER.info("This push has no previous commit")
        return True, None
    if _SHA.fullmatch(before) is None:
        return Failure("version", f"Refusing previous commit {before}")
    text = show_pyproject(before)
    if isinstance(text, Failure):
        return text
    if text is None:
        return False, None
    parsed = version_in_text(text, "previous")
    if isinstance(parsed, Failure):
        return parsed
    return False, parsed


def decide(before: str, after_file: Path) -> ReleaseDecision | Failure:
    """Compare the previous commit with the checked-out pyproject.toml."""
    current = _current_version(after_file)
    if isinstance(current, Failure):
        return current
    previous = _previous_version(before)
    if isinstance(previous, Failure):
        return previous
    new_history, previous_version = previous
    already_released = True
    if previous_version == current and not new_history:
        found = github_release_exists(current)
        if isinstance(found, Failure):
            return found
        already_released = found
    return choose_release(
        previous_version,
        current,
        new_history=new_history,
        already_released=already_released,
    )


def main(argv: list[str] | None = None) -> int:
    """Write ``release`` and ``version`` for the release workflow."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True)
    parser.add_argument("--after-file", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args(argv)
    decision = decide(str(args.before), args.after_file)
    if isinstance(decision, Failure):
        LOGGER.error(decision.message)
        return 1
    write_github_output(args.github_output, decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
