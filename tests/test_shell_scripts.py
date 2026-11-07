"""Packaged PowerShell and shell scripts are the files the program runs."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

import pytest

from cursor_portable.disk import spawn
from cursor_portable.launcher.path_edit import path_block
from cursor_portable.repack import sfx
from cursor_portable.repack.sfx import build_posix_sfx
from cursor_portable.shell import powershell_file_command, shell_text
from scripts.docker_session import _write_stub
from scripts.run_docker_session import _container_command
from scripts.verify import _shellcheck_groups
from tests.test_launcher_scripts import _bash_executable, _posix_directory

_SHELL = Path(__file__).resolve().parent / "shell"
_RUNTIME = frozenset(
    {
        "Get-VolumeListing.ps1",
        "Get-WindowsShortcuts.ps1",
        "Invoke-ElevatedCommand.ps1",
        "Set-WindowsShortcut.ps1",
        "posix-extractor.sh",
        "prepend-path.sh",
    }
)
_CHECKED_IN = _RUNTIME | {"run_docker_session.sh", "session-stub.sh"}
_FAKE_EXTRACTOR = """#!/bin/sh
set -eu
printf '%s\\n' "$@" > "$SFX_LOG"
root=""
for arg in "$@"; do
    case "$arg" in
        -o*) root=${arg#-o} ;;
    esac
done
mkdir -p "$root/python/bin"
printf '%s\\n' '#!/bin/sh' 'printf "%s\\n" "$@" > "$SFX_LOG.argv"' 'exit 0' > "$root/python/bin/python3"
chmod +x "$root/python/bin/python3"
exit 0
"""


def test_shell_directory_is_the_script_set() -> None:
    found = {path.name for path in _SHELL.iterdir() if path.suffix in {".ps1", ".sh"}}
    assert found == _CHECKED_IN


def test_runtime_scripts_match_the_checked_in_files() -> None:
    for name in sorted(_RUNTIME):
        assert shell_text(name) == (_SHELL / name).read_text(encoding="utf-8")


def test_shellcheck_covers_checked_in_shell_scripts() -> None:
    covered = {path.name for _shell, files in _shellcheck_groups() for path in files}
    packaged = {path.name for path in _SHELL.glob("*.sh")}
    assert packaged <= covered
    assert "cursor-portable" in covered
    assert "run_docker_session.sh" in covered


def test_powershell_linter_scans_checked_in_scripts() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts" / "lint_powershell.ps1").read_text(encoding="utf-8")
    assert "tests\\shell" in text
    assert "tests\\launchers" in text


def test_missing_shell_script_fails() -> None:
    with pytest.raises(FileNotFoundError):
        shell_text("missing.ps1")


def test_volume_probe_runs_the_packaged_script() -> None:
    command = powershell_file_command("Get-VolumeListing.ps1")
    assert command[5] == "-File"
    text = Path(command[-1]).read_text(encoding="utf-8")
    assert "MSFT_Disk" in text
    assert "writable" in text


def test_path_block_renders_the_shell_template() -> None:
    template = shell_text("prepend-path.sh").replace("\r\n", "\n")
    assert template.replace("__SEGMENT__", "/opt/py/bin") in path_block("/opt/py/bin")
    quoted = "/opt/o'\\''bin"
    both = path_block(("/opt/o'bin", "/opt/py/Scripts"))
    rendered = template.replace("cursor_portable_bin", "cursor_portable_bin_0")
    assert rendered.replace("__SEGMENT__", quoted) in both
    assert "cursor_portable_bin_1='/opt/py/Scripts'" in both


def test_session_stub_is_the_shell_file(tmp_path: Path) -> None:
    app = tmp_path / "app"
    _write_stub(app)
    written = (app / "cursor").read_text(encoding="utf-8")
    assert written == (_SHELL / "session-stub.sh").read_text(encoding="utf-8")
    assert written.startswith("#!/bin/sh\n")


def test_docker_session_runs_the_shell_file() -> None:
    root = Path(__file__).resolve().parents[1]
    command = _container_command(root)
    assert command[-2:] == ["bash", "/work/tests/shell/run_docker_session.sh"]
    assert (root / "tests" / "shell" / "run_docker_session.sh").is_file()


def test_elevated_disk_command_uses_the_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def fake_execute(
        executable: str,
        parameters: str,
        directory: Path,
        verb: str,
    ) -> int:
        del directory, verb
        seen["executable"] = executable
        seen["parameters"] = parameters
        return 0

    monkeypatch.setattr(spawn, "_shell_execute", fake_execute)
    monkeypatch.setattr(spawn, "_powershell", lambda: Path("powershell.exe"))
    assert spawn._windows_elevated(("cmd.exe", "/c", "echo", "hi")) == (0, "\n")
    assert seen["executable"] == "powershell.exe"
    assert "Invoke-ElevatedCommand.ps1" in seen["parameters"]
    assert "-File" in seen["parameters"]


def test_posix_renderer_rejects_a_bad_template() -> None:
    zeros = {name: 0 for name in sfx._OFFSET_NAMES}
    with pytest.raises(RuntimeError, match="12 digits"):
        sfx._render_posix(
            "EXTRACTOR_OFFSET=000000000000\n", zeros | {"EXTRACTOR_OFFSET": -1}
        )
    with pytest.raises(RuntimeError, match="EXTRACTOR_OFFSET"):
        sfx._render_posix("nope\n", zeros)


def test_posix_template_adds_a_missing_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sfx, "shell_text", lambda _name: "#!/usr/bin/env bash")
    assert sfx._posix_template().endswith("\n")


def test_posix_script_refuses_a_length_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sfx, "_render_posix", lambda template, _values: template + "x")
    with pytest.raises(RuntimeError, match="length"):
        sfx._posix_script(1, 1)


def test_shell_scripts_parse() -> None:
    bash = _bash_executable()
    for _shell, files in _shellcheck_groups():
        for path in files:
            completed = subprocess.run(
                [bash, "-n", str(path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert completed.returncode == 0, f"{path.name}\n{completed.stderr}"


def test_path_block_shell_prepends_the_directory(tmp_path: Path) -> None:
    bash = _bash_executable()
    segment = "/opt/o'bin"
    script = tmp_path / "prepend.sh"
    script.write_bytes(
        f"PATH=/usr/bin\n{path_block(segment)}printf '%s\\n' \"$PATH\"\n".encode()
    )
    completed = subprocess.run(
        [bash, str(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines()[0].startswith(f"{segment}:")


def test_session_stub_waits_until_stop_exists(tmp_path: Path) -> None:
    bash = _bash_executable()
    root = _posix_directory(bash, tmp_path)
    ready = tmp_path / "ready"
    stop = tmp_path / "stop"
    env = os.environ.copy()
    env["CURSOR_SESSION_READY"] = f"{root}/ready"
    env["CURSOR_SESSION_STOP"] = f"{root}/stop"
    process = subprocess.Popen(
        [bash, str(_SHELL / "session-stub.sh")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.is_file():
            if process.poll() is not None:
                break
            time.sleep(0.05)
        assert ready.is_file()
        stop.write_bytes(b"\n")
        _stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    assert ready.read_text(encoding="utf-8").strip() == "ready"


def test_posix_extractor_runs_under_bash(tmp_path: Path) -> None:
    bash = _bash_executable()
    blob = build_posix_sfx(_FAKE_EXTRACTOR.encode("utf-8"), b"archive-bytes")
    (tmp_path / "setup").write_bytes(blob)
    root = _posix_directory(bash, tmp_path)
    env = os.environ.copy()
    env["SFX_LOG"] = f"{root}/sfx-log"
    completed = subprocess.run(
        [bash, f"{root}/setup"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    recorded = (tmp_path / "sfx-log").read_text(encoding="utf-8").splitlines()
    assert recorded[0] == "x"
    assert any(line.endswith("/setup.7z") for line in recorded)
    argv = (tmp_path / "sfx-log.argv").read_text(encoding="utf-8").splitlines()
    assert argv[:2] == ["-m", "cursor_portable"]
