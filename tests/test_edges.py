"""Cover command wiring, session edges, and disk probes."""

from __future__ import annotations

import argparse
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import cursor_portable.__main__ as package_main
from cursor_portable._version import __version__
from cursor_portable.cli.disk_cmd import run_disk_command
from cursor_portable.cli import launch_cmd
from cursor_portable.cli.live import (
    install_payload,
    payload_directory,
    wizard_callbacks,
)
from cursor_portable.disk.commands import DiskPlan, RepairMode, repair_plan
import cursor_portable.disk.elevation as elevation
from cursor_portable.disk.elevation import elevation_failure, elevation_ready
from cursor_portable.disk.listing import (
    load_probed_volumes,
    probe_command,
    volumes_from_diskutil,
    volumes_from_lsblk,
)
from cursor_portable.disk.probe import parse_volume_json, volumes_from_objects
from cursor_portable.disk.volume import Volume
from cursor_portable.download.catalog import cli_install_url
from cursor_portable.download.cli_script import cli_archive_url
from cursor_portable.download.fetch import download_file
from cursor_portable.download.http_client import HttpResponse, UrllibClient
from cursor_portable.failure import Failure
from cursor_portable.host.cleanup import cache_directory, cleanup_session
from cursor_portable.install.apply import install_tree
from cursor_portable.json_io import as_object_list, load_object, string_field
from cursor_portable.launcher.home_link import (
    HomeAction,
    apply_home_link,
    plan_home_link,
)
from cursor_portable.launcher import host_watch
from cursor_portable.launcher.host_watch import (
    desktop_exec,
    desktop_shortcuts,
    read_processes,
    read_shortcuts,
    restore_desktop_text,
    restore_shortcuts,
    stop_processes,
    windows_shortcut_command,
)
from cursor_portable.launcher.launch_plan import (
    LaunchCommand,
    cursor_executable,
    interpreter_path,
    python_segments,
)
from cursor_portable.launcher.links import DirectoryLinker
from cursor_portable.launcher.path_edit import prepend_segments, separator_for
from cursor_portable.launcher.path_store import (
    MemoryPathStore,
    path_store_for,
    prepend_user_path,
)
from cursor_portable.launcher.runner import run_until_exit
from cursor_portable.launcher.session import LaunchHooks, run_launch
from cursor_portable.launcher.session_file import (
    SessionRecord,
    parse_session,
    read_session,
)
from cursor_portable.launcher.shortcuts import Shortcut
import cursor_portable.models as models
from cursor_portable.models import (
    HostSystem,
    InstallChoices,
    InstallScope,
    ProductKind,
    ProfileMode,
    host_system_name,
)
from cursor_portable.profile.copy_profile import (
    _copy_allowlisted_tree,
    _skip_chat,
    copy_home_profile,
    copy_profile,
)
from cursor_portable.profile.settings import set_interpreter
from cursor_portable.profile.sources import ProfileSources
from cursor_portable.repack.archive import run_command, seven_zip_add, seven_zip_extract
from cursor_portable.repack.commands import appimage_extract_command, dmg_detach_command
from cursor_portable.repack.layout import StickLayout
from cursor_portable.repack.meta import (
    read_cursor_version,
    read_version_fields,
    write_version_file,
)
from cursor_portable.repack.product_json import find_product_json, strip_product_file
from cursor_portable.repack.sfx import build_posix_sfx, posix_offsets
import cursor_portable.wizard.__main__ as wizard_main
from scripts.ci_pack import asset_names, asset_url


def _volume(**changes: object) -> Volume:
    values: dict[str, object] = {
        "device_id": "E:",
        "mount_point": "E:\\",
        "label": "STICK",
        "filesystem": "NTFS",
        "size_bytes": 8,
        "removable": True,
        "is_system": False,
        "is_boot": False,
    }
    values.update(changes)
    return Volume(
        device_id=str(values["device_id"]),
        mount_point=str(values["mount_point"]),
        label=str(values["label"]),
        filesystem=str(values["filesystem"]),
        size_bytes=int(str(values["size_bytes"])),
        removable=bool(values["removable"]),
        is_system=bool(values["is_system"]),
        is_boot=bool(values["is_boot"]),
    )


def _choices(profile: ProfileMode = ProfileMode.FRESH) -> InstallChoices:
    return InstallChoices(
        product=ProductKind.BOTH,
        scope=InstallScope.USER,
        profile=profile,
        volume_device="E:",
        typed_label="STICK",
    )


def test_host_watch_probes(tmp_path: Path, monkeypatch: object) -> None:
    command = windows_shortcut_command()
    assert "WScript.Shell" in command[-1]
    assert "ProgramData" in command[-1]
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    ready = desktop / "Cursor.desktop"
    ready.write_text("Exec=/usr/bin/cursor --reuse\n", encoding="utf-8")
    (desktop / "Empty.desktop").write_text("Name=Empty\n", encoding="utf-8")
    found = desktop_shortcuts(tmp_path)
    assert found == (Shortcut(str(ready), "/usr/bin/cursor"),)
    assert desktop_exec("Name=Only\n") == ""
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        stdout = ""
        if command[0] == "tasklist":
            stdout = '"cursor.exe","1234","Console","1","1 K"\n'
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(host_watch, "is_windows", lambda: True)  # type: ignore[attr-defined]
    monkeypatch.setattr(host_watch.subprocess, "run", fake_run)  # type: ignore[attr-defined]
    assert read_processes()[0].pid == 1234
    assert not read_shortcuts()
    stop_processes((1234,))
    restore_shortcuts((Shortcut(r"C:\Cursor.lnk", r"C:\Program\Cursor.exe"),))
    assert [item[0] for item in calls] == [
        "tasklist",
        "powershell",
        "taskkill",
        "powershell",
    ]

    def fake_ps(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:1] == ["ps"] and calls.count(command) == 1:
            return subprocess.CompletedProcess(command, 0, "  9 cursor\n", "")
        return subprocess.CompletedProcess(command, 1, "", "missing")

    calls.clear()
    monkeypatch.setattr(host_watch, "is_windows", lambda: False)  # type: ignore[attr-defined]
    monkeypatch.setattr(host_watch.subprocess, "run", fake_ps)  # type: ignore[attr-defined]
    assert read_processes()[0].pid == 9
    assert not read_processes()
    missing = tmp_path / "gone.desktop"
    restore_shortcuts((Shortcut(str(missing), "/bin/cursor"),))
    restore_shortcuts((Shortcut(str(ready), "/bin/cursor"),))
    assert ready.read_text(encoding="utf-8").startswith("Exec=/bin/cursor")
    killed: list[int] = []
    monkeypatch.setattr(
        host_watch.os, "kill", lambda pid, _sig: killed.append(pid)
    )  # type: ignore[attr-defined]
    stop_processes((1234,))
    assert killed == [1234]

    def fail_kill(_pid: int, _sig: int) -> None:
        raise OSError(_pid)

    monkeypatch.setattr(host_watch.os, "kill", fail_kill)  # type: ignore[attr-defined]
    stop_processes((5,))


def test_models_and_small_helpers(monkeypatch: object) -> None:
    monkeypatch.setattr(models.platform, "system", lambda: "Windows")  # type: ignore[attr-defined]
    assert host_system_name() is HostSystem.WINDOWS
    monkeypatch.setattr(models.platform, "system", lambda: "Darwin")  # type: ignore[attr-defined]
    assert host_system_name() is HostSystem.MACOS
    monkeypatch.setattr(models.platform, "system", lambda: "Linux")  # type: ignore[attr-defined]
    assert host_system_name() is HostSystem.LINUX
    monkeypatch.setattr(models.platform, "system", lambda: "Plan9")  # type: ignore[attr-defined]
    try:
        host_system_name()
    except ValueError as exc:
        assert "Plan9" in str(exc)
    assert cli_install_url(windows=True).endswith("win32=true")
    assert separator_for(HostSystem.WINDOWS) == ";"
    assert separator_for(HostSystem.MACOS) == ":"
    assert string_field({"version": 1}, "version") is None
    assert isinstance(as_object_list({"a": 1}), Failure)
    assert as_object_list([1]) == [1]
    assert isinstance(load_object('{"a": 1, "1": 2}'), dict)
    bad_key = load_object("{1: 2}")
    assert isinstance(bad_key, Failure)
    assert probe_command(HostSystem.MACOS)[0] == "diskutil"
    plist = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>AllDisksAndPartitions</key><array><dict>
<key>DeviceIdentifier</key><string>disk2</string>
<key>Ejectable</key><true/>
<key>Partitions</key><array><dict>
<key>DeviceIdentifier</key><string>disk2s1</string>
<key>MountPoint</key><string>/Volumes/STICK</string>
<key>VolumeName</key><string>STICK</string>
<key>Size</key><integer>8</integer>
<key>Content</key><string>ExFAT</string>
</dict></array>
</dict></array></dict></plist>
"""
    volumes = volumes_from_diskutil(plist)
    assert not isinstance(volumes, Failure)
    assert volumes[0].filesystem == "exFAT"
    assert volumes[0].removable is True
    internal = plist.replace(
        "<key>Ejectable</key><true/>",
        "<key>Internal</key><true/><key>Ejectable</key><true/>",
    )
    internal_volumes = volumes_from_diskutil(internal)
    assert not isinstance(internal_volumes, Failure)
    assert internal_volumes[0].removable is False
    assert isinstance(volumes_from_diskutil("not a plist"), Failure)
    fields = read_version_fields(Path("missing-version.json"))
    assert fields[2] is InstallScope.USER
    assert prepend_segments(
        "C:\\Windows", ("D:\\py", "D:\\py\\Scripts"), ";"
    ).startswith("D:\\py")
    assert (
        desktop_exec("Name=Cursor\nExec=/usr/bin/cursor --reuse\n") == "/usr/bin/cursor"
    )
    assert restore_desktop_text("Exec=/tmp/cache\n", "/usr/bin/cursor").startswith(
        "Exec=/usr/bin/cursor"
    )
    release = {
        "assets": [
            {
                "name": "cpython.tar.gz",
                "browser_download_url": "https://example.invalid/a",
            }
        ]
    }
    assert asset_names(release) == ("cpython.tar.gz",)
    assert asset_url(release, "cpython.tar.gz") == "https://example.invalid/a"
    assert not asset_names([])
    linux = repair_plan(
        _volume(device_id="/dev/sdb1", filesystem="ext4"),
        "STICK",
        HostSystem.LINUX,
        RepairMode.CHECK,
        busy=False,
    )
    assert not isinstance(linux, Failure)
    assert "-n" in linux.command
    assert dmg_detach_command(Path("/Volumes/Cursor"))[0] == "hdiutil"
    assert appimage_extract_command(Path("Cursor.AppImage"))[-1] == "--appimage-extract"


def test_http_client(monkeypatch: object) -> None:
    class Response:
        status = 200
        headers = {"Content-Type": "application/octet-stream"}

        def geturl(self) -> str:
            return "https://downloads.cursor.com/CursorSetup.exe"

        def read(self) -> bytes:
            return b"exe"

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(
        "cursor_portable.download.http_client.urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    loaded = UrllibClient().get(
        "https://downloads.cursor.com/CursorSetup.exe", timeout=1
    )
    assert not isinstance(loaded, Failure)
    assert loaded.body == b"exe"

    def raise_http(*_args: object, **_kwargs: object) -> object:
        raise HTTPError(
            "https://downloads.cursor.com",
            404,
            "missing",
            hdrs=None,  # type: ignore[arg-type]
            fp=BytesIO(b""),
        )

    monkeypatch.setattr("cursor_portable.download.http_client.urlopen", raise_http)
    assert isinstance(
        UrllibClient().get("https://downloads.cursor.com/x", timeout=1), Failure
    )

    def raise_url(*_args: object, **_kwargs: object) -> object:
        raise URLError("offline")

    monkeypatch.setattr("cursor_portable.download.http_client.urlopen", raise_url)
    assert isinstance(
        UrllibClient().get("https://downloads.cursor.com/x", timeout=1), Failure
    )


def test_commands_and_session_edges(tmp_path: Path) -> None:
    failed = run_command([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert isinstance(failed, Failure)
    assert run_command([sys.executable, "-c", "import sys; sys.exit(0)"]) is None
    assert seven_zip_add("7z", tmp_path / "a.7z", tmp_path)[0] == "7z"
    assert seven_zip_extract("7z", tmp_path / "a.7z", tmp_path)[0] == "7z"
    try:
        build_posix_sfx(b"", b"archive")
    except RuntimeError as exc:
        assert "extractor" in str(exc)
    assert isinstance(posix_offsets(b"not a script"), Failure)
    assert find_product_json(tmp_path) is None
    broken = tmp_path / "product.json"
    broken.parent.mkdir(parents=True, exist_ok=True)
    resources = tmp_path / "resources" / "app"
    resources.mkdir(parents=True)
    (resources / "product.json").write_text("{", encoding="utf-8")
    assert isinstance(strip_product_file(resources / "product.json"), Failure)
    command = LaunchCommand(
        argv=(sys.executable, "-c", "raise SystemExit(0)"),
        extra_env=(("SAMPLE", "1"),),
    )
    assert (
        run_until_exit(command, {}, lambda: None, watch_seconds=0, poll_seconds=0) == 0
    )
    silenced = LaunchCommand(
        argv=(
            sys.executable,
            "-c",
            "import os,sys; raise SystemExit(os.getenv('NODE_NO_WARNINGS') != '1')",
        ),
        extra_env=(("NODE_NO_WARNINGS", "1"),),
    )
    assert (
        run_until_exit(
            silenced,
            {"NODE_NO_WARNINGS": "0"},
            lambda: None,
            watch_seconds=0,
            poll_seconds=0,
        )
        == 0
    )
    layout = StickLayout.from_root(tmp_path / "CursorPortable")
    layout.app.mkdir(parents=True)
    (layout.app / "Cursor.exe").write_text("old", encoding="utf-8")
    write_version_file(
        layout.version_file,
        cursor_version="1.0.0",
        commit="",
        scope=InstallScope.USER,
        product=ProductKind.IDE,
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "Cursor.exe").write_text("new", encoding="utf-8")
    (cache / "resources" / "app").mkdir(parents=True)
    (cache / "resources" / "app" / "product.json").write_text(
        '{"version": "2.0.0", "target": "user"}',
        encoding="utf-8",
    )
    (cache / "version.json").write_text(
        '{"cursor_version": "2.0.0", "commit": "", "product": "ide", "scope": "user"}\n',
        encoding="utf-8",
    )
    (cache / ".pending-sync").write_text("pending\n", encoding="utf-8")
    store = MemoryPathStore("C:\\Windows")
    hooks = LaunchHooks(
        store=store,
        linker=DirectoryLinker(),
        run=lambda _command, on_tick: on_tick() or 0,
        processes=lambda: tuple(),
        stop=lambda _pids: None,
        read_shortcuts=lambda: (
            Shortcut("installed.lnk", "C:\\Program\\Cursor.exe"),
            Shortcut("installed.lnk", str(cache / "Cursor.exe")),
        ),
        restore_shortcuts=lambda items: items,
        alive=lambda pid: pid == 7,
    )
    session = tmp_path / "host" / "session.txt"
    session.parent.mkdir()
    session.write_text("pid=7\npath_separator=;\n", encoding="utf-8")
    busy = run_launch(
        layout,
        session.parent,
        cache,
        tmp_path / "scratch",
        tmp_path / "dot",
        HostSystem.WINDOWS,
        hooks,
        pid=8,
    )
    assert busy == 3
    quiet = LaunchHooks(
        store=MemoryPathStore("C:\\Windows"),
        linker=DirectoryLinker(),
        run=lambda _command, on_tick: on_tick() or 0,
        processes=lambda: tuple(),
        stop=lambda _pids: None,
        read_shortcuts=lambda: (),
        restore_shortcuts=lambda items: items,
        alive=lambda _pid: False,
    )
    finished = run_launch(
        layout,
        session.parent,
        cache,
        tmp_path / "scratch",
        tmp_path / "dot",
        HostSystem.WINDOWS,
        quiet,
        pid=8,
    )
    assert finished == 0
    assert read_cursor_version(layout.version_file) == "2.0.0"
    assert read_cursor_version(tmp_path / "missing.json") is None


def test_install_payload_and_launch_commands(
    tmp_path: Path, monkeypatch: object
) -> None:
    payload = tmp_path / "payload"
    app = payload / "ide-user" / "resources" / "app"
    app.mkdir(parents=True)
    (app / "product.json").write_text('{"version": "1.0.0"}', encoding="utf-8")
    (payload / "ide-user" / "Cursor.exe").write_text("x", encoding="utf-8")
    (payload / "cli").mkdir()
    (payload / "cli" / "cursor.exe").write_text("cli", encoding="utf-8")
    (payload / "python").mkdir()
    (payload / "python" / "python.exe").write_text("py", encoding="utf-8")
    (payload / "tool").mkdir()
    mount = tmp_path / "drive"
    mount.mkdir()
    volume = Volume(
        device_id="E:",
        mount_point=str(mount),
        label="STICK",
        filesystem="exFAT",
        size_bytes=10,
        removable=True,
        is_system=False,
        is_boot=False,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.cli.live.acquire_payload",
        lambda *_args, **_kwargs: Failure("payload", "offline"),
    )
    assert (
        install_payload(_choices(), volume, None, system=HostSystem.WINDOWS) is not None
    )
    assert (
        install_payload(
            _choices(),
            _volume(filesystem="exFAT", mount_point=str(mount)),
            payload,
            system=HostSystem.WINDOWS,
        )
        is not None
    )
    copied = install_payload(
        _choices(),
        _volume(mount_point=str(mount)),
        payload,
        system=HostSystem.WINDOWS,
    )
    assert copied is None
    assert (mount / "CursorPortable" / "cli" / "cursor.exe").is_file()
    missing = install_tree(
        tmp_path / "absent",
        StickLayout.from_mount(mount),
        _choices(),
        HostSystem.WINDOWS,
        sources=None,
        cursor_running=False,
    )
    assert isinstance(missing, Failure)
    home = tmp_path / "profile"
    appdata = tmp_path / "roaming"
    (appdata / "Cursor").mkdir(parents=True)
    dot = home / ".cursor"
    dot.mkdir(parents=True)
    sources = ProfileSources(
        user_data=appdata / "Cursor",
        dot_cursor=dot,
        extensions=dot / "extensions",
        argv=dot / "argv.json",
    )
    assert (
        install_tree(
            payload / "ide-user",
            StickLayout.from_mount(tmp_path / "second"),
            _choices(ProfileMode.COPY),
            HostSystem.WINDOWS,
            sources=sources,
            cursor_running=False,
        )
        is None
    )
    settings = tmp_path / "settings.json"
    settings.write_text('{"editor.fontSize": 14}\n', encoding="utf-8")
    assert set_interpreter(settings, Path("python.exe")) is True
    assert "python.defaultInterpreterPath" in settings.read_text(encoding="utf-8")
    assert payload_directory(["--payload-dir", str(payload)]) == payload
    assert (
        payload_directory(["--payload", str(payload / "nope.7z")])
        == payload / "nope.7z"
    )
    callbacks = wizard_callbacks(
        volumes=(_volume(),), payload=payload, system=HostSystem.WINDOWS
    )
    assert callbacks.volumes()[0].label == "STICK"  # type: ignore[index]
    assert callbacks.install(_choices(), _volume(mount_point=str(mount))) is None
    archive = tmp_path / "pack.7z"
    archive.write_bytes(b"not-a-real-archive")
    extracted = install_payload(_choices(), volume, archive, system=HostSystem.WINDOWS)
    assert isinstance(extracted, Failure)
    monkeypatch.setattr(launch_cmd, "host_system_name", lambda: HostSystem.LINUX)
    monkeypatch.setattr(launch_cmd, "run_launch", lambda *_args, **_kwargs: 0)
    assert launch_cmd.launch_root(tmp_path / "CursorPortable") == 0
    monkeypatch.setattr(launch_cmd, "_host_root", lambda _system: tmp_path / "host")
    monkeypatch.setattr(
        launch_cmd,
        "path_store_for",
        lambda *_args, **_kwargs: MemoryPathStore("/usr/bin", "plain"),
    )
    monkeypatch.setattr(launch_cmd, "_shortcut_candidates", lambda: ())
    host = tmp_path / "host"
    host.mkdir()
    (host / "runtime" / "app").mkdir(parents=True)
    (host / "session.txt").write_text(
        "path_separator=:\npath_segment=/opt/portable\npid=1\n",
        encoding="utf-8",
    )
    assert launch_cmd.uninstall_host() == 0
    assert launch_cmd._alive(0) is False
    assert launch_cmd._alive(os_pid()) is True
    launch_cmd._stop_nothing((1,))
    launch_cmd._restore_nothing((Shortcut("a", "b"),))
    described = launch_cmd.describe_launch(
        tmp_path / "CursorPortable", HostSystem.LINUX
    )
    assert any("disk-cache-dir" in part for part in described)
    assert cursor_executable(tmp_path, HostSystem.MACOS).name == "Cursor"
    assert cursor_executable(tmp_path, HostSystem.LINUX).name == "cursor"
    assert (
        interpreter_path(StickLayout.from_root(tmp_path), HostSystem.LINUX).name
        == "python3"
    )
    assert python_segments(StickLayout.from_root(tmp_path), HostSystem.WINDOWS)
    try:
        path_store_for(HostSystem.LINUX, None)
    except RuntimeError as exc:
        assert "rc path" in str(exc)
    prepend_user_path(MemoryPathStore("A;B"), ("A",), ";")
    record = parse_session("# comment\nnoise\npid=4\n")
    assert record.pid == "4"
    assert read_session(tmp_path / "absent") is None
    link = tmp_path / "real-dot"
    link.mkdir()
    backup = Path(str(link) + ".host-backup-cursor-portable")
    backup.mkdir()
    target = tmp_path / "CursorPortable" / "data" / "dot-cursor"
    target.mkdir(parents=True)
    planned = plan_home_link(
        link,
        target,
        current_target=None,
        link_exists=True,
        backup_exists=True,
    )
    assert planned.action is HomeAction.HEAL
    assert isinstance(apply_home_link(planned, DirectoryLinker()), Failure)
    cleanup_session(
        SessionRecord(home_link=str(tmp_path / "missing-link"), home_backup=""),
        MemoryPathStore(),
        DirectoryLinker(),
        delete_cache=True,
    )


def test_probe_and_cli(tmp_path: Path, monkeypatch: object, capsys: object) -> None:
    from cursor_portable.cli.main import main

    class Completed:
        def __init__(self, code: int, out: str, err: str) -> None:
            self.returncode = code
            self.stdout = out
            self.stderr = err

    payload = (
        '{"device_id":"E:","mount_point":"E:\\\\","label":"STICK",'
        '"filesystem":"exFAT","size_bytes":4,"removable":true,'
        '"is_system":false,"is_boot":false}'
    )
    monkeypatch.setattr(
        "cursor_portable.disk.listing.subprocess.run",
        lambda *_args, **_kwargs: Completed(0, payload, ""),
    )
    loaded = load_probed_volumes(HostSystem.WINDOWS)
    assert not isinstance(loaded, Failure)
    monkeypatch.setattr(
        "cursor_portable.disk.listing.subprocess.run",
        lambda *_args, **_kwargs: Completed(1, "", "denied"),
    )
    assert isinstance(load_probed_volumes(HostSystem.LINUX), Failure)
    assert isinstance(volumes_from_lsblk("{"), Failure)
    assert isinstance(volumes_from_lsblk("[]"), Failure)
    assert isinstance(volumes_from_lsblk('{"blockdevices": 1}'), Failure)
    skipped = volumes_from_lsblk(
        '{"blockdevices":[1, {"path":"/dev/sdb1","mountpoint":"/mnt","label":"S",'
        '"fstype":"exfat","size":8,"rm":"1"}]}'
    )
    assert not isinstance(skipped, Failure)
    assert isinstance(load_probed_volumes(HostSystem.MACOS), Failure)
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__  # type: ignore[attr-defined]
    native = {
        HostSystem.WINDOWS: "NTFS",
        HostSystem.MACOS: "APFS",
        HostSystem.LINUX: "ext4",
    }[host_system_name()]
    assert (
        main(
            [
                "repair",
                "--device",
                "E:",
                "--mount",
                "E:\\",
                "--label",
                "STICK",
                "--filesystem",
                native,
                "--typed",
                "STICK",
                "--removable",
            ]
        )
        == 0
    )
    module = tmp_path / "sfx"
    archive = tmp_path / "app.7z"
    output = tmp_path / "setup.exe"
    module.write_bytes(b"MZ")
    archive.write_bytes(b"7z")
    assert (
        main(
            [
                "pack-sfx",
                "--module",
                str(module),
                "--archive",
                str(archive),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert output.is_file()
    monkeypatch.setattr("cursor_portable.cli.main._launch", lambda _root: 0)
    monkeypatch.setattr("cursor_portable.cli.main._uninstall_host", lambda: 0)
    monkeypatch.setattr(
        "cursor_portable.cli.main.subprocess.run",
        lambda *_args, **_kwargs: Completed(0, "", ""),
    )
    assert main(["launch", "--root", str(tmp_path)]) == 0
    assert main(["uninstall-host"]) == 0
    assert main(["uninstall"]) == 0
    assert main(["wizard"]) == 0
    monkeypatch.setattr(
        "cursor_portable.cli.main.host_system_name",
        lambda: (_ for _ in ()).throw(ValueError("unknown")),
    )
    monkeypatch.setattr(
        "cursor_portable.disk.filesystem.shutil.which",
        lambda _name: "/sbin/mkfs.ext4",
    )
    assert (
        main(
            [
                "format-volume",
                "--device",
                "/dev/sdb1",
                "--mount",
                "/mnt/stick",
                "--label",
                "STICK",
                "--filesystem",
                "NTFS",
                "--typed",
                "STICK",
                "--removable",
                "--size",
                "8",
            ]
        )
        == 0
    )


def os_pid() -> int:
    """Return the current process id."""
    return os.getpid()


def test_remaining_branches(tmp_path: Path, monkeypatch: object) -> None:
    assert package_main.main is not None
    assert _skip_chat("History", skip_chats=True, include_backup=False) is True
    assert _skip_chat("state.vscdb", skip_chats=True, include_backup=False) is True
    assert (
        _skip_chat("state.vscdb.backup", skip_chats=False, include_backup=False) is True
    )
    assert _skip_chat("state.vscdb", skip_chats=False, include_backup=True) is False
    user_data = tmp_path / "Cursor"
    (user_data / "User" / "globalStorage").mkdir(parents=True)
    (user_data / "User" / "globalStorage" / "state.vscdb").write_text(
        "db", encoding="utf-8"
    )
    (user_data / "User" / "History").mkdir()
    (user_data / "Local State").write_text("state", encoding="utf-8")
    (user_data / "ignored.txt").write_text("nope", encoding="utf-8")
    dot = tmp_path / "dot"
    (dot / "skills").mkdir(parents=True)
    (dot / "skills" / "demo").mkdir()
    (dot / "mcp.json").write_text("{}", encoding="utf-8")
    layout = StickLayout.from_root(tmp_path / "stick")
    sources = ProfileSources(
        user_data=user_data,
        dot_cursor=dot,
        extensions=tmp_path / "missing-extensions",
        argv=tmp_path / "argv.json",
    )
    sources.argv.write_text("{}\n", encoding="utf-8")
    report = copy_profile(
        sources, layout, cursor_running=True, include_state_backup=False
    )
    assert report.skipped_chats is True
    assert not (layout.user_data / "User" / "globalStorage" / "state.vscdb").exists()
    _copy_allowlisted_tree(
        tmp_path / "absent-profile",
        tmp_path / "dest",
        directories=frozenset(),
        files=frozenset(),
        skip_chats=False,
        include_backup=False,
    )
    copy_home_profile(tmp_path / "absent-home", tmp_path / "home-dest")
    assert isinstance(volumes_from_objects({"no": "list"}), Failure)
    assert isinstance(volumes_from_objects([1]), Failure)
    assert isinstance(
        volumes_from_objects([{"device_id": "", "mount_point": ""}]), Failure
    )
    parsed = volumes_from_objects(
        [
            {
                "device_id": "E:",
                "mount_point": "E:\\",
                "size_bytes": "12",
                "removable": "yes",
                "is_system": False,
                "is_boot": "1",
            }
        ]
    )
    assert not isinstance(parsed, Failure)
    assert parsed[0].size_bytes == 12
    assert isinstance(parse_volume_json("{"), Failure)

    class GoodClient:
        def get(self, url: str, *, timeout: float) -> HttpResponse:
            del timeout
            return HttpResponse(status=200, url=url, body=b"payload", headers={})

    class BadClient:
        def get(self, url: str, *, timeout: float) -> Failure:
            del url, timeout
            return Failure("http", "down")

    saved = tmp_path / "downloads" / "cursor.bin"
    assert download_file("https://example.invalid/a", saved, GoodClient(), minimum_bytes=1) is None  # type: ignore[arg-type]
    too_small = download_file(
        "https://example.invalid/a",
        tmp_path / "small.bin",
        GoodClient(),  # type: ignore[arg-type]
        minimum_bytes=100,
    )
    assert isinstance(too_small, Failure)
    assert isinstance(
        download_file(
            "https://example.invalid/a",
            tmp_path / "none.bin",
            BadClient(),  # type: ignore[arg-type]
            minimum_bytes=1,
        ),
        Failure,
    )
    assert payload_directory([]) is None
    archive = tmp_path / "inner.7z"
    archive.write_bytes(b"7z")
    monkeypatch.setattr(
        "cursor_portable.cli.live.run_command",
        lambda _command: None,
    )
    extracted = install_payload(
        _choices(),
        Volume(
            device_id="E:",
            mount_point=str(tmp_path / "mounted"),
            label="STICK",
            filesystem="exFAT",
            size_bytes=4,
            removable=True,
            is_system=False,
            is_boot=False,
        ),
        archive,
        system=HostSystem.WINDOWS,
    )
    assert isinstance(extracted, Failure)
    mismatch = install_payload(
        _choices(),
        _volume(label="OTHER"),
        archive,
        system=HostSystem.WINDOWS,
    )
    assert isinstance(mismatch, Failure)
    blank = install_payload(
        _choices(),
        _volume(label=""),
        archive,
        system=HostSystem.WINDOWS,
    )
    assert isinstance(blank, Failure)
    assert blank.message == "This drive has no label. Type E: to confirm."
    system_disk = install_payload(
        _choices(),
        _volume(is_system=True),
        archive,
        system=HostSystem.WINDOWS,
    )
    assert isinstance(system_disk, Failure)
    monkeypatch.setattr(
        "cursor_portable.cli.live.profile_sources",
        lambda _system: Failure("profile", "missing"),
    )
    copied = install_payload(
        _choices(ProfileMode.COPY),
        _volume(),
        tmp_path / "payload-dir",
        system=HostSystem.WINDOWS,
    )
    assert isinstance(copied, Failure)
    monkeypatch.setattr(
        "cursor_portable.cli.live.execute_plan_result",
        lambda _plan: Failure("disk", "Disk command exited 4."),
    )
    callbacks = wizard_callbacks(system=HostSystem.LINUX, probe=True)
    monkeypatch.setattr(
        "cursor_portable.cli.live.load_probed_volumes",
        lambda _system: (),
    )
    assert callbacks.volumes() == ()
    disk_plan = DiskPlan(command=("echo",), needs_elevation=False, summary="check")
    assert isinstance(callbacks.run_format(disk_plan), Failure)
    target = tmp_path / "CursorPortable" / "data" / "dot-cursor"
    target.mkdir(parents=True)
    host_link = tmp_path / "home-cursor"
    host_link.mkdir()
    (host_link / "keep.txt").write_text("kept", encoding="utf-8")
    created = plan_home_link(
        host_link,
        target,
        current_target=None,
        link_exists=True,
        backup_exists=False,
    )
    assert created.action is HomeAction.CREATE
    assert apply_home_link(created, DirectoryLinker()) is None
    already = plan_home_link(
        host_link,
        target,
        current_target=str(target),
        link_exists=True,
        backup_exists=True,
    )
    assert already.action is HomeAction.ALREADY
    assert apply_home_link(already, DirectoryLinker()) is None
    other = tmp_path / "OtherCursorPortable" / "data" / "dot-cursor"
    other.mkdir(parents=True)
    healed = plan_home_link(
        host_link,
        other,
        current_target=str(target),
        link_exists=True,
        backup_exists=False,
    )
    assert healed.action is HomeAction.HEAL
    missing_target = tmp_path / "fresh" / "CursorPortable" / "data" / "dot-cursor"
    missing = plan_home_link(
        tmp_path / "unused-link",
        missing_target,
        current_target=None,
        link_exists=False,
        backup_exists=False,
    )
    assert missing.action is HomeAction.MISSING_TARGET
    assert apply_home_link(missing, DirectoryLinker()) is None
    monkeypatch.setattr("cursor_portable.launcher.links.is_windows", lambda: False)
    monkeypatch.setattr("cursor_portable.cli.launch_cmd.is_windows", lambda: False)
    created_link = DirectoryLinker().create(tmp_path / "sym", tmp_path / "sym-target")
    assert created_link is not None or (tmp_path / "sym").is_symlink()
    assert DirectoryLinker().remove(tmp_path / "absent-link") is None
    monkeypatch.setattr(
        launch_cmd.os,
        "kill",
        lambda _pid, _signal: (_ for _ in ()).throw(OSError("no")),
    )
    assert launch_cmd._alive(4) is False
    assert (
        launch_cmd._run_command(
            LaunchCommand(
                argv=(sys.executable, "-c", "raise SystemExit(0)"),
                extra_env=(),
            ),
            lambda: None,
        )
        == 0
    )
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    (desktop / "Cursor Portable.lnk").write_text("lnk", encoding="utf-8")
    (desktop / "notes.txt").write_text("no", encoding="utf-8")
    programs = (
        tmp_path / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    )
    programs.mkdir(parents=True)
    (programs / "cursor-portable.desktop").write_text("desk", encoding="utf-8")
    monkeypatch.setattr(launch_cmd.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    found = launch_cmd._shortcut_candidates()
    assert len(found) == 2
    monkeypatch.setattr(wizard_main, "run_wizard", lambda _callbacks: 0)
    monkeypatch.setattr(wizard_main, "host_system_name", lambda: HostSystem.LINUX)
    monkeypatch.setattr(wizard_main, "payload_directory", lambda: None)
    assert wizard_main.main() == 0


def test_elevation_redirect_and_cache(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(elevation, "is_windows", lambda: False)  # type: ignore[attr-defined]
    assert elevation_ready(HostSystem.WINDOWS) is True

    monkeypatch.setattr(elevation, "is_windows", lambda: True)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        elevation.ctypes,
        "windll",
        SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 1)),
        raising=False,
    )
    assert elevation_ready(HostSystem.WINDOWS) is True
    assert elevation_failure(HostSystem.WINDOWS) is None
    monkeypatch.setattr(  # type: ignore[attr-defined]
        elevation.ctypes,
        "windll",
        SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 0)),
        raising=False,
    )
    refused = elevation_failure(HostSystem.WINDOWS)
    assert isinstance(refused, Failure)
    assert refused.code == "elevation"

    def deny() -> int:
        raise OSError("denied")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        elevation.ctypes,
        "windll",
        SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=deny)),
        raising=False,
    )
    assert elevation_ready(HostSystem.WINDOWS) is False
    monkeypatch.setattr(elevation.ctypes, "windll", object(), raising=False)  # type: ignore[attr-defined]
    assert elevation_ready(HostSystem.WINDOWS) is False
    monkeypatch.setattr(elevation.os, "geteuid", lambda: 0, raising=False)  # type: ignore[attr-defined]
    assert elevation_ready(HostSystem.LINUX) is True
    monkeypatch.setattr(elevation.os, "geteuid", lambda: 4, raising=False)  # type: ignore[attr-defined]
    assert isinstance(elevation_failure(HostSystem.LINUX), Failure)

    class Redirect:
        status = 200
        headers: dict[str, str] = {}

        def geturl(self) -> str:
            return "http://example.com/setup"

        def read(self) -> bytes:
            return b"no"

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.download.http_client.urlopen",
        lambda *_args, **_kwargs: Redirect(),
    )
    assert isinstance(
        UrllibClient().get("https://downloads.cursor.com/a", timeout=1), Failure
    )

    class OddStatus:
        status: object = "200"
        headers: dict[str, str] = {}

        def geturl(self) -> str:
            return "https://downloads.cursor.com/a"

        def read(self) -> bytes:
            return b"no"

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.download.http_client.urlopen",
        lambda *_args, **_kwargs: OddStatus(),
    )
    assert isinstance(
        UrllibClient().get("https://downloads.cursor.com/a", timeout=1), Failure
    )
    cache = cache_directory(tmp_path / "host", tmp_path / "stick")
    assert cache.name == "app"
    assert cache.parent.parent.name == "runtime"

    def fail_tick() -> None:
        raise RuntimeError("tick")

    assert (
        run_until_exit(
            LaunchCommand(
                argv=(sys.executable, "-c", "raise SystemExit(0)"),
                extra_env=(),
            ),
            {},
            fail_tick,
            watch_seconds=0,
            poll_seconds=0,
        )
        == 0
    )
    setattr(host_watch._warn_probe_once, "done", False)
    assert host_watch._run([sys.executable, "-c", "import sys; sys.exit(2)"]) is None
    assert host_watch._run([sys.executable, "-c", "import sys; sys.exit(2)"]) is None
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.cli.disk_cmd.volume_busy",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.disk.apply.capture_command",
        lambda *_args, **_kwargs: Failure("elevation", "need admin"),
    )
    args = argparse.Namespace(
        device="E:",
        mount="E:\\",
        label="STICK",
        filesystem="exFAT",
        typed="STICK",
        removable=True,
        system_disk=False,
        boot_disk=False,
        size=10,
        apply=True,
    )
    assert (
        run_disk_command(args, HostSystem.WINDOWS, repair=True, probed=(_volume(),))
        == 1
    )
    assert run_disk_command(args, HostSystem.WINDOWS, repair=True, probed=()) == 1
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.cli.live.execute_plan_result",
        lambda _plan: Failure("elevation", "need admin"),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.cli.live.apply_repair",
        lambda _plan: Failure("elevation", "need admin"),
    )
    callbacks = wizard_callbacks(system=HostSystem.WINDOWS)
    plan = DiskPlan(command=("echo",), needs_elevation=True, summary="format")
    assert isinstance(callbacks.run_format(plan), Failure)
    assert isinstance(callbacks.run_repair(plan), Failure)


def test_finish_without_payload_downloads(tmp_path: Path, monkeypatch: object) -> None:
    mount = tmp_path / "drive"
    mount.mkdir()
    prepared = tmp_path / "prepared"
    app = prepared / "ide-user" / "resources" / "app"
    app.mkdir(parents=True)
    (app / "product.json").write_text(
        '{"version": "1.2.3", "target": "user"}\n',
        encoding="utf-8",
    )
    (prepared / "ide-user" / "Cursor.exe").write_text("cursor", encoding="utf-8")
    messages: list[str] = []

    def fake_acquire(
        choices: object,
        system: object,
        destination: Path,
        progress: object = None,
    ) -> None:
        del choices, system
        if callable(progress):
            progress("Downloading Cursor")
        shutil.copytree(prepared, destination, dirs_exist_ok=True)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.cli.live.payload_cache",
        lambda: tmp_path / "cache",
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.cli.live.acquire_payload",
        fake_acquire,
    )
    volume = Volume(
        device_id="E:",
        mount_point=str(mount),
        label="STICK",
        filesystem="NTFS",
        size_bytes=10,
        removable=True,
        is_system=False,
        is_boot=False,
    )
    result = install_payload(
        _choices(),
        volume,
        None,
        system=HostSystem.WINDOWS,
        progress=messages.append,
    )
    assert result is None
    assert "Downloading Cursor" in messages
    product = mount / "CursorPortable" / "app" / "resources" / "app" / "product.json"
    assert product.is_file()
    assert "target" not in product.read_text(encoding="utf-8")


def test_cli_archive_url_fills_install_script_placeholders() -> None:
    base = "https://downloads.cursor.com/lab/2026.11.05-86047df/"
    bash = f'DOWNLOAD_URL="{base}${{OS}}/${{ARCH}}/agent-cli-package.tar.gz"\n'
    windows = (
        f"$downloadUrl = '{base}'\n"
        '$fullUrl = $UrlPrefix + "windows/$architecture/agent-cli-package.zip"\n'
    )
    assert cli_archive_url(bash, HostSystem.LINUX, "x64") == (
        f"{base}linux/x64/agent-cli-package.tar.gz"
    )
    assert cli_archive_url(bash, HostSystem.MACOS, "arm64") == (
        f"{base}darwin/arm64/agent-cli-package.tar.gz"
    )
    assert cli_archive_url(windows, HostSystem.WINDOWS, "arm64") == (
        f"{base}windows/arm64/agent-cli-package.zip"
    )
    refused = (
        cli_archive_url(windows, HostSystem.LINUX, "x64"),
        cli_archive_url(bash, HostSystem.LINUX, "ia32"),
        cli_archive_url(
            "curl https://downloads.cursor.com/lab/${NOT}/a.tar.gz\n",
            HostSystem.LINUX,
            "x64",
        ),
    )
    assert all(isinstance(item, Failure) for item in refused)
    broken = f'DOWNLOAD_URL="{base}${{OS}}/${{ARCH}}/${{CHANNEL}}/agent.tar.gz"\n'
    assert isinstance(cli_archive_url(broken, HostSystem.LINUX, "x64"), Failure)
