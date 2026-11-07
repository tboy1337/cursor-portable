"""Generated launcher files kept for blinter, shellcheck, and Pester."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

from cursor_portable.repack.launchers import (
    unix_launcher_text,
    windows_launcher_text,
    write_launcher_scripts,
)

_REPO_LAUNCHERS = Path(__file__).resolve().parents[1] / "scripts" / "launchers"
_NAMES = (
    "CursorPortable.cmd",
    "cursor-portable",
    "Start-HeadlessCursor.ps1",
    "Hide-ConsoleWindow.ps1",
    "Show-ConsoleWindow.ps1",
)


def _files(directory: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def _command_bodies(text: str) -> list[str]:
    bodies: list[str] = []
    for line in text.splitlines():
        marker = "-Command "
        if "powershell.exe" not in line.casefold() or marker not in line:
            continue
        command = line.split(marker, 1)[1].strip()
        if len(command) >= 2 and command.startswith('"') and command.endswith('"'):
            command = command[1:-1]
        bodies.append(command)
    return bodies


def _inline_powershell(script: str) -> str:
    return "; ".join(line.strip() for line in script.splitlines() if line.strip())


def test_write_launcher_scripts_emits_lint_targets(tmp_path: Path) -> None:
    write_launcher_scripts(tmp_path)
    written = _files(tmp_path)
    assert tuple(sorted(written)) == tuple(sorted(_NAMES))
    cmd = written["CursorPortable.cmd"]
    bash = written["cursor-portable"]
    assert cmd == windows_launcher_text().encode("utf-8")
    assert bash == unix_launcher_text().encode("utf-8")
    assert b"\r" not in bash
    assert bash.startswith(b"#!/usr/bin/env bash\n")
    assert cmd.startswith(b"@echo off\r\n")
    bodies = _command_bodies(cmd.decode("ascii"))
    snippets = [
        written["Start-HeadlessCursor.ps1"],
        written["Hide-ConsoleWindow.ps1"],
        written["Show-ConsoleWindow.ps1"],
    ]
    assert len(bodies) == len(snippets)
    for body, snippet in zip(bodies, snippets, strict=True):
        text = snippet.decode("utf-8")
        assert "\r" not in text
        assert '"' not in text
        assert _inline_powershell(text) == body
    assert "ShowWindow($handle, 0)" in snippets[1].decode("ascii")
    assert "ShowWindow($handle, 9)" in snippets[2].decode("ascii")


def test_repository_launcher_scripts_match_writer(tmp_path: Path) -> None:
    write_launcher_scripts(tmp_path)
    assert _REPO_LAUNCHERS.is_dir()
    assert _files(_REPO_LAUNCHERS) == _files(tmp_path)


def test_windows_launcher_expands_the_exit_code_on_the_endlocal_line() -> None:
    assert "endlocal & exit /b %EXIT_CODE%\r\n" in windows_launcher_text()


_PYTHON_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$0" > "$STUB_LOG"
printf '%s\\n' "$PYTHONPATH" >> "$STUB_LOG"
printf '%s\\n' "$@" >> "$STUB_LOG"
exit "${STUB_EXIT:-0}"
"""


def _bash_candidates() -> list[Path]:
    candidates: list[Path] = []
    git = shutil.which("git")
    if git is not None:
        git_path = Path(git).resolve()
        candidates.append(git_path.parent.parent / "bin" / "bash.exe")
        candidates.append(git_path.parent / "bash.exe")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Git" / "bin" / "bash.exe")
    candidates.extend(
        (
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        )
    )
    found = shutil.which("bash")
    if found is not None:
        candidates.append(Path(found))
    return candidates


def _bash_executable() -> str:
    seen: set[str] = set()
    for candidate in _bash_candidates():
        if not candidate.is_file():
            continue
        resolved = str(candidate.resolve())
        folded = resolved.casefold()
        if resolved in seen or "system32" in folded or "windowsapps" in folded:
            continue
        seen.add(resolved)
        completed = subprocess.run(
            [resolved, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = f"{completed.stdout}{completed.stderr}"
        if completed.returncode == 0 and "bash" in output.casefold():
            return resolved
    if os.environ.get("CI"):
        pytest.fail("bash is required on CI")
    pytest.skip("bash is not installed")


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _posix_directory(bash: str, directory: Path) -> str:
    completed = subprocess.run(
        [bash, "-c", 'cd -- "$1" && pwd', "pwd", str(directory)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _run_checked_in_launcher(
    bash: str,
    root: Path,
    *,
    log: Path,
    exit_code: str = "0",
) -> subprocess.CompletedProcess[str]:
    script = root / "cursor-portable"
    script.write_bytes((_REPO_LAUNCHERS / "cursor-portable").read_bytes())
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    environment = os.environ.copy()
    environment["STUB_LOG"] = str(log)
    environment["STUB_EXIT"] = exit_code
    environment["CDPATH"] = str(root.parent)
    return subprocess.run(
        [bash, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        cwd=root.parent,
        timeout=30,
    )


def test_checked_in_unix_launcher_uses_python3(tmp_path: Path) -> None:
    bash = _bash_executable()
    root = tmp_path / "stick"
    log = tmp_path / "python3.txt"
    _write_executable(root / "python" / "bin" / "python3", _PYTHON_STUB)
    _write_executable(root / "python" / "python.exe", _PYTHON_STUB)
    completed = _run_checked_in_launcher(bash, root, log=log)
    assert completed.returncode == 0, completed.stderr
    recorded = log.read_text(encoding="utf-8").splitlines()
    posix_root = _posix_directory(bash, root)
    assert recorded[0].endswith("/python3")
    assert recorded[1:] == [
        f"{posix_root}/tool",
        "-m",
        "cursor_portable",
        "launch",
        "--root",
        posix_root,
    ]


def test_checked_in_unix_launcher_falls_back_to_python_exe(tmp_path: Path) -> None:
    bash = _bash_executable()
    root = tmp_path / "stick"
    log = tmp_path / "python-exe.txt"
    _write_executable(root / "python" / "python.exe", _PYTHON_STUB)
    completed = _run_checked_in_launcher(bash, root, log=log, exit_code="4")
    assert completed.returncode == 4, completed.stderr
    recorded = log.read_text(encoding="utf-8").splitlines()
    posix_root = _posix_directory(bash, root)
    assert recorded[0].endswith("/python.exe")
    assert recorded[1] == f"{posix_root}/tool"
    assert recorded[-1] == posix_root
