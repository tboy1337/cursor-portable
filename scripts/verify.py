#!/usr/bin/env python3
"""Run the local quality gate."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
CHECK_DIRS = ("src", "scripts", "tests")
_LAUNCHERS = ROOT / "scripts" / "launchers"
_WINDOWS_STEPS = frozenset({"pytest", "powershell"})


def _python(*args: str) -> list[str]:
    return [sys.executable, *args]


def _shellcheck() -> str:
    """Return the shellcheck binary installed beside this interpreter.

    A Unix virtualenv stores its Python as a symlink. Resolving that link
    points at the base interpreter, which does not contain console scripts.
    """
    name = "shellcheck.exe" if sys.platform == "win32" else "shellcheck"
    return str(Path(sys.executable).parent / name)


def _powershell_lint() -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "lint_powershell.ps1"),
    ]


def _run(command: list[str]) -> int:
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=ROOT, check=False)
    return completed.returncode


def _steps(*, skip_pytest: bool) -> list[tuple[str, list[str]]]:
    steps = [
        (
            "autopep8",
            _python(
                "-m", "autopep8", "--diff", "--select=W291,W293", "-r", *CHECK_DIRS
            ),
        ),
        ("isort", _python("-m", "isort", "--check-only", "--diff", *CHECK_DIRS)),
        ("pyproject-fmt", _python("-m", "pyproject_fmt", "--check", "pyproject.toml")),
        ("black", _python("-m", "black", "--check", *CHECK_DIRS)),
        ("yamllint", _python("-m", "yamllint", ".github", ".yamllint.yaml")),
        ("mypy", _python("-m", "mypy")),
        ("pylint", _python("-m", "pylint", *CHECK_DIRS)),
        (
            "bandit",
            _python("-m", "bandit", "-c", "pyproject.toml", "-r", "src", "scripts"),
        ),
        (
            "complexipy",
            _python(
                "-m",
                "complexipy.cli",
                "src",
                "--max-complexity-allowed",
                "15",
            ),
        ),
        ("pip-audit", _python("-m", "pip_audit")),
        (
            "blinter",
            _python(
                "-m",
                "blinter",
                "--fail-on",
                "warning",
                str(_LAUNCHERS / "CursorPortable.cmd"),
            ),
        ),
        (
            "shellcheck",
            [
                _shellcheck(),
                "--shell=bash",
                "--severity=warning",
                str(_LAUNCHERS / "cursor-portable"),
            ],
        ),
    ]
    if sys.platform == "win32":
        steps.append(("powershell", _powershell_lint()))
    if not skip_pytest:
        steps.append(("pytest", _python("-m", "pytest")))
    return steps


def main(argv: list[str] | None = None) -> int:
    """Run each gate and stop on the first failure."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--windows-only", action="store_true")
    args = parser.parse_args(argv)
    if args.windows_only and sys.platform != "win32":
        print("Skipping Windows-only checks on this host.")
        return 0
    for name, command in _steps(skip_pytest=args.skip_pytest):
        if args.windows_only and name not in _WINDOWS_STEPS:
            continue
        code = _run(command)
        if code != 0:
            print(f"{name} failed with exit code {code}", file=sys.stderr)
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
