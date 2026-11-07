"""Windows launcher console behavior."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from cursor_portable.constants import HOME_BACKUP_SUFFIX
from cursor_portable.launcher import headless
from cursor_portable.launcher.headless import HEADLESS_ENV, notify_headless
from cursor_portable.launcher.host_watch import write_managed_shortcut
from cursor_portable.launcher.links import DirectoryLinker
from cursor_portable.launcher.path_store import MemoryPathStore
from cursor_portable.launcher.session import LaunchHooks, run_launch
from cursor_portable.models import HostSystem, InstallScope, ProductKind
from cursor_portable.repack.launchers import windows_launcher_text
from cursor_portable.repack.layout import StickLayout
from cursor_portable.repack.meta import write_version_file

_STUB = r"""
using System;
using System.IO;
using System.Text;
public class LaunchStub {
    public static int Main(string[] args) {
        string path = Environment.GetEnvironmentVariable("CURSOR_PORTABLE_STUB_LOG");
        if (string.IsNullOrEmpty(path)) {
            return 2;
        }
        var builder = new StringBuilder();
        builder.AppendLine(Environment.GetCommandLineArgs()[0]);
        string pythonPath = Environment.GetEnvironmentVariable("PYTHONPATH");
        builder.AppendLine(pythonPath == null ? "" : pythonPath);
        foreach (string arg in args) {
            builder.AppendLine(arg);
        }
        File.WriteAllText(path, builder.ToString());
        string code = Environment.GetEnvironmentVariable("CURSOR_PORTABLE_STUB_EXIT");
        int parsed;
        if (!int.TryParse(code, out parsed)) {
            return 0;
        }
        return parsed;
    }
}
"""


def test_windows_launcher_has_no_lasting_console() -> None:
    text = windows_launcher_text().replace("\r\n", "\n")
    assert "\r" not in text
    assert "--console" in text
    assert "CURSOR_PORTABLE_CONSOLE" in text
    assert "CURSOR_PORTABLE_HEADLESS" in text
    assert "python\\pythonw.exe" in text
    assert "python\\python.exe" in text
    assert "ShowWindow($handle, 0)" in text
    assert "ShowWindow($handle, 9)" in text
    assert "__HIDE_CONSOLE__" not in text
    for line in text.splitlines():
        if "-Command" not in line:
            continue
        command = line.split("-Command", 1)[1].strip().strip('"')
        assert '"' not in command


def test_windows_launcher_uses_crlf() -> None:
    text = windows_launcher_text()
    assert text.startswith("@echo off\r\n")
    assert "\n" not in text.replace("\r\n", "")


def test_managed_shortcut_starts_minimized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def capture(command: list[str]) -> str:
        seen.append(command)
        return ""

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr("cursor_portable.launcher.host_watch._run", capture)
    write_managed_shortcut(tmp_path / "CursorPortable.cmd", windows=True)
    assert seen
    assert seen[0][seen[0].index("-WindowStyle") + 1] == "7"
    shortcut = Path(seen[0][seen[0].index("-File") + 1])
    assert "CreateShortcut" in shortcut.read_text(encoding="utf-8")


def test_notify_headless_only_without_a_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown: list[str] = []
    monkeypatch.setattr("cursor_portable.launcher.headless.is_windows", lambda: True)
    monkeypatch.setattr("cursor_portable.launcher.headless._console_window", lambda: 0)
    monkeypatch.setattr("cursor_portable.launcher.headless._message_box", shown.append)
    monkeypatch.setenv(HEADLESS_ENV, "1")
    notify_headless("already running")
    assert shown == ["already running"]
    monkeypatch.setattr("cursor_portable.launcher.headless._console_window", lambda: 4)
    notify_headless("attached")
    assert shown == ["already running"]
    monkeypatch.delenv(HEADLESS_ENV)
    monkeypatch.setattr("cursor_portable.launcher.headless._console_window", lambda: 0)
    notify_headless("quiet")
    assert shown == ["already running"]
    monkeypatch.setenv(HEADLESS_ENV, "1")
    monkeypatch.setattr("cursor_portable.launcher.headless.is_windows", lambda: False)
    notify_headless("other system")
    assert shown == ["already running"]


def test_win_dll_requires_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(
        "cursor_portable.launcher.headless.ctypes.WinDLL", raising=False
    )
    with pytest.raises(OSError, match="WinDLL"):
        headless._win_dll("kernel32")


def test_headless_dialog_uses_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, ...]] = []

    class _Call:
        def __init__(self, value: int) -> None:
            self.argtypes: object = None
            self.restype: object = None
            self._value = value

        def __call__(self, *args: object) -> int:
            calls.append(args)
            return self._value

    class _Dll:
        def __init__(self, name: str, use_last_error: bool = True) -> None:
            del name, use_last_error
            setattr(self, "GetConsoleWindow", _Call(7))
            setattr(self, "MessageBoxW", _Call(1))

    monkeypatch.setattr(headless.ctypes, "WinDLL", _Dll, raising=False)
    assert headless._console_window() == 7
    headless._message_box("could not start")
    assert calls[-1][1:] == (
        "could not start",
        "Cursor Portable",
        headless._ERROR_DIALOG,
    )
    broken = _Dll("user32")
    setattr(broken, "GetConsoleWindow", _Call(0))
    monkeypatch.setattr(
        headless.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: broken,
        raising=False,
    )
    assert headless._console_window() == 0

    def fail(message: str) -> None:
        del message
        raise OSError("dialog failed")

    monkeypatch.setenv(HEADLESS_ENV, "1")
    monkeypatch.setattr("cursor_portable.launcher.headless.is_windows", lambda: True)
    monkeypatch.setattr("cursor_portable.launcher.headless._console_window", lambda: 0)
    monkeypatch.setattr("cursor_portable.launcher.headless._message_box", fail)
    notify_headless("still running")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console launcher")
def test_double_clicked_launcher_uses_pythonw(tmp_path: Path) -> None:
    stub = _compile_stub(tmp_path / "compiler")
    root = tmp_path / "stick"
    log = tmp_path / "pythonw.txt"
    _install_launcher(root, stub, pythonw=True)
    completed = _run_cmd(root / "CursorPortable.cmd", log)
    assert completed.returncode == 0, completed.stderr
    recorded = _wait_for_log(log)
    assert recorded[0].casefold().endswith("pythonw.exe")
    assert recorded[1].casefold().endswith("\\tool")
    assert "launch" in recorded
    special = tmp_path / "A & B"
    special_log = tmp_path / "amp.txt"
    _install_launcher(special, stub, pythonw=True)
    completed = _run_explorer(special / "CursorPortable.cmd", special_log)
    assert completed.returncode == 0, completed.stderr
    recorded = _wait_for_log(special_log)
    assert recorded[0].casefold().endswith("pythonw.exe")
    assert "A & B" in recorded[1]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console launcher")
def test_console_switch_keeps_python_and_exit_code(tmp_path: Path) -> None:
    root = tmp_path / "stick"
    log = tmp_path / "python.txt"
    _install_launcher(root, _compile_stub(tmp_path / "compiler"), pythonw=True)
    completed = _run_cmd(
        root / "CursorPortable.cmd",
        log,
        ["--console"],
        extra_env={"CURSOR_PORTABLE_STUB_EXIT": "4"},
    )
    assert completed.returncode == 4
    recorded = _wait_for_log(log)
    assert recorded[0].casefold().endswith("python.exe")
    env = _launcher_env(log)
    env["CURSOR_PORTABLE_CONSOLE"] = "1"
    other = tmp_path / "env.txt"
    env["CURSOR_PORTABLE_STUB_LOG"] = str(other)
    completed = _run_cmd(root / "CursorPortable.cmd", other, environment=env)
    assert completed.returncode == 0
    assert _wait_for_log(other)[0].casefold().endswith("python.exe")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console launcher")
def test_called_launcher_stays_in_the_caller_console(tmp_path: Path) -> None:
    root = tmp_path / "stick"
    log = tmp_path / "called.txt"
    _install_launcher(root, _compile_stub(tmp_path / "compiler"), pythonw=True)
    runner = root / "run-visible.cmd"
    runner.write_text(
        '@echo off\r\ncall "%~dp0CursorPortable.cmd"\r\n',
        encoding="ascii",
    )
    completed = _run_cmd(runner, log)
    assert completed.returncode == 0
    assert _wait_for_log(log)[0].casefold().endswith("python.exe")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console launcher")
def test_missing_pythonw_hides_and_still_launches(tmp_path: Path) -> None:
    root = tmp_path / "stick"
    log = tmp_path / "hidden.txt"
    _install_launcher(root, _compile_stub(tmp_path / "compiler"), pythonw=False)
    env = _launcher_env(log)
    env["CURSOR_PORTABLE_LAUNCH"] = str(root / "CursorPortable.cmd")
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Start-Process -FilePath $env:ComSpec -ArgumentList "
            "@('/d','/c', $env:CURSOR_PORTABLE_LAUNCH) "
            "-WindowStyle Hidden -Wait",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    recorded = _wait_for_log(log)
    assert recorded[0].casefold().endswith("python.exe")


def _install_launcher(root: Path, stub: Path, *, pythonw: bool) -> None:
    python = root / "python"
    python.mkdir(parents=True)
    (python / "python.exe").write_bytes(stub.read_bytes())
    if pythonw:
        (python / "pythonw.exe").write_bytes(stub.read_bytes())
    (root / "CursorPortable.cmd").write_bytes(windows_launcher_text().encode("ascii"))


def _compile_stub(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / "stub.exe"
    if exe.is_file():
        return exe
    script = directory / "compile.ps1"
    script.write_text(
        "$source = @'\n" + _STUB + "'@\n"
        "Add-Type -TypeDefinition $source -OutputAssembly '"
        + str(exe)
        + "' -OutputType WindowsApplication\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert exe.is_file()
    return exe


def _launcher_env(log: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop(HEADLESS_ENV, None)
    env.pop("CURSOR_PORTABLE_CONSOLE", None)
    env.pop("CURSOR_PORTABLE_STUB_EXIT", None)
    env["CURSOR_PORTABLE_STUB_LOG"] = str(log)
    return env


def _run_cmd(
    launcher: Path,
    log: Path,
    args: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = _launcher_env(log) if environment is None else dict(environment)
    if extra_env:
        env.update(extra_env)
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.run(
        ["cmd.exe", "/d", "/c", str(launcher), *(args or [])],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        creationflags=flags,
    )


def _run_explorer(launcher: Path, log: Path) -> subprocess.CompletedProcess[str]:
    """Start a launcher the way Explorer double-clicks a path containing &."""
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.run(
        f'cmd.exe /d /c ""{launcher}""',
        check=False,
        capture_output=True,
        text=True,
        env=_launcher_env(log),
        timeout=30,
        creationflags=flags,
    )


def test_locked_home_folder_is_left_in_place(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    layout = StickLayout.from_root(tmp_path / "CursorPortable")
    layout.app.mkdir(parents=True)
    (layout.app / "Cursor.exe").write_text("exe", encoding="utf-8")
    write_version_file(
        layout.version_file,
        cursor_version="1.2.3",
        commit="abc",
        scope=InstallScope.USER,
        product=ProductKind.IDE,
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "Cursor.exe").write_text("exe", encoding="utf-8")
    write_version_file(
        cache / "version.json",
        cursor_version="1.2.3",
        commit="abc",
        scope=InstallScope.USER,
        product=ProductKind.IDE,
    )
    home = tmp_path / ".cursor"
    home.mkdir()
    (home / "keep.txt").write_text("mine", encoding="utf-8")
    original = Path.rename

    def deny(self: Path, target: Path) -> Path:
        if self == home:
            raise PermissionError(13, "Access is denied")
        return original(self, target)

    monkeypatch.setattr(Path, "rename", deny)  # type: ignore[attr-defined]
    store = MemoryPathStore(r"C:\Windows")
    hooks = LaunchHooks(
        store=store,
        linker=DirectoryLinker(),
        run=lambda _command, _on_tick: 0,
        processes=lambda: (),
        stop=lambda _pids: None,
        read_shortcuts=lambda: (),
        restore_shortcuts=lambda _items: None,
        alive=lambda _pid: False,
    )
    code = run_launch(
        layout,
        tmp_path / "host",
        cache,
        tmp_path / "scratch",
        home,
        HostSystem.WINDOWS,
        hooks,
        pid=42,
    )
    assert code == 1
    assert (home / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert not Path(str(home) + HOME_BACKUP_SUFFIX).exists()
    assert store.value == r"C:\Windows"
    assert not (cache / ".pending-sync").exists()


def _wait_for_log(path: Path) -> list[str]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not path.is_file():
        time.sleep(0.05)
    assert path.is_file()
    return path.read_text(encoding="utf-8").splitlines()
