"""Behavior tests for download, disk, profile, launcher, and packing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tarfile
import zipfile

from cursor_portable.cli.disk_cmd import run_disk_command
from cursor_portable.cli.main import main
from cursor_portable.constants import UNINSTALLER_NAMES
from cursor_portable.disk.apply import apply_repair
from cursor_portable.disk.commands import (
    RepairMode,
    format_plan,
    refuses_reboot_repair,
    repair_plan,
)
from cursor_portable.disk.filesystem import display_filesystem, filesystem_for
from cursor_portable.disk.guards import guard_volume, require_filesystem
from cursor_portable.disk.listing import (
    apply_mount_writable,
    linux_probe_command,
    parse_mount_writable,
    volumes_from_lsblk,
    windows_probe_command,
)
from cursor_portable.disk.probe import parse_volume_json
from cursor_portable.disk.volume import (
    Volume,
    confirmation_token,
    label_matches,
    safe_label,
    validate_device,
    volume_name,
)
from cursor_portable.download.catalog import (
    api_download_url,
    ide_target,
    setup_asset_name,
)
from cursor_portable.download.cli_script import (
    extract_download_urls,
    select_cli_archive,
)
from cursor_portable.download.fetch import download_file
from cursor_portable.download.http_client import HttpResponse, UrllibClient
from cursor_portable.download.resolve import file_name_from_response, resolve_ide
from cursor_portable.download.sum_file import expected_sha256, render_sha256sums
from cursor_portable.download.verify_file import check_download, file_sha256
from cursor_portable.failure import Failure
from cursor_portable.host.activity import cursor_editor_running, volume_busy
from cursor_portable.host.cleanup import (
    cleanup_session,
    default_host_root,
    delete_managed_shortcuts,
)
from cursor_portable.install.apply import install_tree, scope_dir
from cursor_portable.json_io import dump_object, load_object
from cursor_portable.launcher.cache_sync import (
    SyncDirection,
    choose_sync,
    sync_after_exit,
)
from cursor_portable.launcher.home_link import (
    HomeAction,
    apply_home_link,
    is_our_home_target,
    plan_home_link,
)
from cursor_portable.launcher.host_watch import write_managed_shortcut
from cursor_portable.launcher.launch_plan import build_launch, python_segments
from cursor_portable.launcher.links import DirectoryLinker, link_target
from cursor_portable.launcher.path_edit import (
    insert_path_block,
    path_block,
    prepend_segment,
    remove_path_block,
    remove_segment,
)
import cursor_portable.launcher.path_store as path_store_module
from cursor_portable.launcher.path_store import (
    MemoryPathStore,
    RcPathStore,
    WinregPathStore,
    prepend_user_path,
    restore_user_path,
    update_rc_block,
)
from cursor_portable.launcher.processes import (
    ProcessInfo,
    installer_pids,
    is_cursor_installer_name,
)
from cursor_portable.launcher.profile_sync import sync_profile
from cursor_portable.launcher.reports import (
    parse_ps,
    parse_shortcut_report,
    parse_tasklist_csv,
)
from cursor_portable.launcher.session import LaunchHooks, run_launch
from cursor_portable.launcher.session_file import (
    SessionRecord,
    parse_session,
    write_session,
)
from cursor_portable.launcher.shortcuts import (
    Shortcut,
    is_portable_target,
    managed_shortcut,
    restorations,
    with_snapshots,
)
from cursor_portable.models import (
    HostSystem,
    InstallChoices,
    InstallScope,
    ProductKind,
    ProfileMode,
    host_system_name,
)
from cursor_portable.profile.copy_profile import copy_profile
from cursor_portable.profile.settings import set_interpreter
from cursor_portable.profile.sources import profile_sources
from cursor_portable.python_runtime.pin import select_python_asset
from cursor_portable.repack.commands import extract_command
from cursor_portable.repack.copy_tree import copy_tree
from cursor_portable.repack.extract_ide import (
    InstallerKind,
    application_directory,
    extract_ide,
    installer_kind,
)
from cursor_portable.repack.layout import StickLayout
from cursor_portable.repack.meta import (
    read_cursor_version,
    read_stick_identity,
    write_version_file,
)
from cursor_portable.repack.product_json import (
    application_root,
    find_product_json,
    read_product_version,
    strip_install_target,
    strip_product_file,
)
from cursor_portable.repack.sfx import (
    build_posix_sfx,
    build_windows_sfx,
    posix_offsets,
    windows_sfx_config,
)
from cursor_portable.wizard.flow import (
    DriveDisposition,
    WizardStep,
    classify_drive,
    next_step,
    step_sequence,
    summary_lines,
)


def _volume(**kwargs: object) -> Volume:
    base: dict[str, object] = {
        "device_id": "E:",
        "mount_point": "E:\\",
        "label": "STICK",
        "filesystem": "NTFS",
        "size_bytes": 100,
        "removable": True,
        "is_system": False,
        "is_boot": False,
    }
    base.update(kwargs)
    return Volume(
        device_id=str(base["device_id"]),
        mount_point=str(base["mount_point"]),
        label=str(base["label"]),
        filesystem=str(base["filesystem"]),
        size_bytes=int(str(base["size_bytes"])),
        removable=bool(base["removable"]),
        is_system=bool(base["is_system"]),
        is_boot=bool(base["is_boot"]),
        writable=bool(base.get("writable", True)),
    )


class FakeClient:
    """HTTP client that returns one canned response."""

    def __init__(self, response: HttpResponse | Failure) -> None:
        self.response = response

    def get(self, url: str, *, timeout: float) -> HttpResponse | Failure:
        del url, timeout
        return self.response


def test_catalog_and_cli_script() -> None:
    assert isinstance(ide_target("win32-x64-user"), type(ide_target("darwin-arm64")))
    assert isinstance(ide_target("nope"), Failure)
    assert "win32-x64-user" in api_download_url("win32-x64-user")
    assert setup_asset_name(HostSystem.WINDOWS, "x64").endswith(".exe")
    assert setup_asset_name(HostSystem.LINUX, "arm64").endswith(".run")
    script = "curl https://downloads.cursor.com/lab/agent.tar.gz\n"
    assert extract_download_urls(script) == (
        "https://downloads.cursor.com/lab/agent.tar.gz",
    )
    assert select_cli_archive(script) == "https://downloads.cursor.com/lab/agent.tar.gz"
    assert isinstance(select_cli_archive("no urls"), Failure)
    plain = select_cli_archive("see https://downloads.cursor.com/lab/readme")
    assert plain == "https://downloads.cursor.com/lab/readme"


def test_download_checks(tmp_path: Path) -> None:
    target = tmp_path / "file.bin"
    target.write_bytes(b"abc")
    assert file_sha256(target) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert check_download(target, minimum_bytes=10, expected_sha256=None) is not None
    assert check_download(target, minimum_bytes=1, expected_sha256="dead") is not None
    assert (
        check_download(target, minimum_bytes=1, expected_sha256=file_sha256(target))
        is None
    )
    sums = render_sha256sums((("abc", "one.bin"),))
    assert expected_sha256(sums, "one.bin") == "abc"
    assert expected_sha256("nope", "one.bin") is None
    response = HttpResponse(
        status=200,
        url="https://downloads.cursor.com/CursorUserSetup-x64-1.2.3.exe",
        body=b"xyz",
        headers={"content-disposition": 'attachment; filename="CursorSetup.exe"'},
    )
    assert file_name_from_response(response) == "CursorSetup.exe"
    nameless = HttpResponse(200, "https://downloads.cursor.com/", b"", {})
    assert file_name_from_response(nameless) == "cursor-download"
    resolved = resolve_ide("win32-x64-user", FakeClient(response))
    assert not isinstance(resolved, Failure)
    assert resolved.file_name == "CursorSetup.exe"
    catalog = HttpResponse(
        status=200,
        url="https://cursor.com/api/download?platform=win32-x64-user",
        body=(
            b'{"downloadUrl":"https://downloads.cursor.com/CursorUserSetup-x64-3.28.7.exe"}'
        ),
        headers={},
    )
    from_json = resolve_ide("win32-x64-user", FakeClient(catalog))
    assert not isinstance(from_json, Failure)
    assert from_json.file_name == "CursorUserSetup-x64-3.28.7.exe"
    assert from_json.url.endswith("CursorUserSetup-x64-3.28.7.exe")
    missing_url = HttpResponse(
        status=200,
        url="https://cursor.com/api/download?platform=win32-x64-user",
        body=b'{"version":"3.28.7"}',
        headers={},
    )
    assert isinstance(resolve_ide("win32-x64-user", FakeClient(missing_url)), Failure)
    not_object = HttpResponse(
        status=200,
        url="https://cursor.com/api/download?platform=win32-x64-user",
        body=b"[1]",
        headers={},
    )
    listed = resolve_ide("win32-x64-user", FakeClient(not_object))
    assert not isinstance(listed, Failure)
    assert listed.url == not_object.url
    nameless_url = HttpResponse(
        status=200,
        url="https://cursor.com/api/download?platform=win32-x64-user",
        body=b'{"downloadUrl":"https://downloads.cursor.com/"}',
        headers={},
    )
    assert isinstance(resolve_ide("win32-x64-user", FakeClient(nameless_url)), Failure)
    failed = resolve_ide("missing", FakeClient(response))
    assert isinstance(failed, Failure)
    http_failed = resolve_ide(
        "win32-x64-user",
        FakeClient(Failure("http", "nope")),
    )
    assert isinstance(http_failed, Failure)
    bad_status = resolve_ide(
        "win32-x64-user",
        FakeClient(HttpResponse(404, "https://example.com/a", b"", {})),
    )
    assert isinstance(bad_status, Failure)
    saved = download_file(
        "https://downloads.cursor.com/a",
        tmp_path / "saved.bin",
        FakeClient(HttpResponse(200, "https://downloads.cursor.com/a", b"hello", {})),
        minimum_bytes=1,
    )
    assert saved is None
    refused = UrllibClient().get("http://example.com", timeout=1)
    assert isinstance(refused, Failure)


def test_product_json_and_copy(tmp_path: Path) -> None:
    app = tmp_path / "app" / "resources" / "app"
    app.mkdir(parents=True)
    product = app / "product.json"
    product.write_text(
        json.dumps(
            {
                "version": "3.1.0",
                "commit": "abc",
                "target": "user",
                "nested": {"version": "9.9.9"},
            }
        ),
        encoding="utf-8",
    )
    assert find_product_json(tmp_path / "app") == product
    assert strip_product_file(product) is None
    loaded = load_object(product.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    assert "target" not in loaded
    assert read_product_version(loaded) == "3.1.0"
    assert read_product_version({"version": "nope"}) == "unknown"
    assert strip_install_target({"name": "Cursor"}) == {"name": "Cursor"}
    assert application_root(product) == tmp_path / "app"
    mac_product = (
        tmp_path / "Cursor.app" / "Contents" / "Resources" / "app" / "product.json"
    )
    mac_product.parent.mkdir(parents=True)
    mac_product.write_text("{}", encoding="utf-8")
    assert application_root(mac_product) == tmp_path / "Cursor.app"
    assert find_product_json(tmp_path / "Cursor.app") == mac_product
    assert installer_kind(Path("Cursor.AppImage")) is InstallerKind.APPIMAGE
    assert installer_kind(Path("Cursor.dmg")) is InstallerKind.DMG
    assert installer_kind(Path("Cursor-1.2.3.tar.gz")) is InstallerKind.TAR
    assert isinstance(installer_kind(Path("notes.txt")), Failure)
    source = tmp_path / "src"
    (source / "unins000.exe").parent.mkdir(parents=True)
    (source / "unins000.exe").write_text("nope", encoding="utf-8")
    (source / "Cursor.exe").write_text("app", encoding="utf-8")
    assert "unins000.exe" in UNINSTALLER_NAMES
    copied = copy_tree(source, tmp_path / "dest")
    assert copied == 1
    assert not (tmp_path / "dest" / "unins000.exe").exists()
    assert copy_tree(source, tmp_path / "dest") == 0
    (source / "data").mkdir()
    (source / "data" / "state.txt").write_text("chat", encoding="utf-8")
    (source / "version.json").write_text("{}\n", encoding="utf-8")
    assert (
        copy_tree(
            source,
            tmp_path / "dest",
            skip_dirs=frozenset({"data"}),
            skip_root_files=frozenset({"version.json"}),
        )
        == 0
    )
    assert not (tmp_path / "dest" / "data").exists()
    assert not (tmp_path / "dest" / "version.json").exists()
    assert isinstance(load_object("["), Failure)
    assert isinstance(load_object("[]"), Failure)
    assert dump_object({"b": 1}).startswith("{")


def test_disk_guards() -> None:
    stick = _volume()
    assert guard_volume(stick, HostSystem.WINDOWS, busy=False) is None
    assert isinstance(
        guard_volume(_volume(is_system=True), HostSystem.WINDOWS, busy=False), Failure
    )
    assert isinstance(
        guard_volume(_volume(removable=False), HostSystem.WINDOWS, busy=False), Failure
    )
    assert isinstance(
        guard_volume(_volume(device_id="not"), HostSystem.WINDOWS, busy=False), Failure
    )
    assert isinstance(guard_volume(stick, HostSystem.WINDOWS, busy=True), Failure)
    assert require_filesystem(_volume(filesystem="FAT32"), "ntfs") is not None
    assert require_filesystem(_volume(), "ntfs") is None
    assert confirmation_token(_volume(label="")) == "E:"
    assert label_matches(stick, "STICK") is True
    blank = _volume(label="", filesystem="FAT32")
    assert label_matches(blank, "E:") is True
    assert label_matches(blank, "E:\\") is True
    assert label_matches(blank, "e:") is True
    assert label_matches(blank, "E") is True
    assert label_matches(blank, "") is False
    assert label_matches(blank, "F:") is False
    assert label_matches(stick, "E:") is False
    linux_blank = _volume(
        device_id="/dev/sdb1",
        mount_point="/mnt/usb",
        label="",
        filesystem="vfat",
    )
    assert label_matches(linux_blank, "/dev/sdb1") is True
    assert label_matches(linux_blank, "/mnt/usb/") is True
    assert label_matches(linux_blank, "/dev/sdb2") is False
    unlabeled = format_plan(blank, "E:\\", HostSystem.WINDOWS, busy=False)
    assert not isinstance(unlabeled, Failure)
    assert unlabeled.command[0] == "format.com"
    assert "/FS:NTFS" in unlabeled.command
    refused = format_plan(blank, "", HostSystem.WINDOWS, busy=False)
    assert isinstance(refused, Failure)
    assert "E:" in refused.message
    refused_repair = repair_plan(
        _volume(label="", filesystem="FAT32"),
        "",
        HostSystem.WINDOWS,
        RepairMode.APPLY,
        busy=False,
    )
    assert isinstance(refused_repair, Failure)
    assert "E:" in refused_repair.message
    assert isinstance(safe_label(_volume(label="ok")), str)
    assert volume_name(_volume(label=""), "ntfs") == "CURSOR"
    assert len(volume_name(_volume(label="ABCDEFGHIJKLMNOPQRSTUVWXYZ"), "ext4")) == 16
    assert validate_device("/dev/sdb1", HostSystem.LINUX) is None
    assert isinstance(validate_device("../etc", HostSystem.LINUX), Failure)
    plan = format_plan(
        _volume(filesystem="FAT32"), "STICK", HostSystem.WINDOWS, busy=False
    )
    assert not isinstance(plan, Failure)
    assert plan.command[0] == "format.com"
    assert isinstance(
        format_plan(stick, "STICK", HostSystem.WINDOWS, busy=False), Failure
    )
    assert isinstance(
        format_plan(_volume(filesystem="FAT32"), "NO", HostSystem.WINDOWS, busy=False),
        Failure,
    )
    mac = format_plan(
        _volume(device_id="/dev/disk2", filesystem="FAT32"),
        "STICK",
        HostSystem.MACOS,
        busy=False,
    )
    assert not isinstance(mac, Failure)
    assert mac.command[:3] == ("diskutil", "eraseVolume", "APFS")
    linux = format_plan(
        _volume(device_id="/dev/sdb1", filesystem="vfat"),
        "STICK",
        HostSystem.LINUX,
        busy=False,
        available_tools=frozenset({"mkfs.ext4", "mkfs.ntfs"}),
    )
    assert not isinstance(linux, Failure)
    assert linux.command[0] == "mkfs.ext4"
    repair = repair_plan(
        stick, "STICK", HostSystem.WINDOWS, RepairMode.CHECK, busy=False
    )
    assert not isinstance(repair, Failure)
    assert repair.command == ("chkdsk", "E:")
    apply = repair_plan(
        _volume(device_id="/dev/sdb1", filesystem="ext4"),
        "STICK",
        HostSystem.LINUX,
        RepairMode.APPLY,
        busy=False,
    )
    assert not isinstance(apply, Failure)
    assert apply.command[0] == "e2fsck"
    mac_repair = repair_plan(
        _volume(device_id="/dev/disk2", filesystem="APFS"),
        "STICK",
        HostSystem.MACOS,
        RepairMode.APPLY,
        busy=False,
    )
    assert not isinstance(mac_repair, Failure)
    assert refuses_reboot_repair("scheduled to run at reboot") is True
    assert refuses_reboot_repair("clean") is False


class _Completed:
    def __init__(self, code: int, out: str) -> None:
        self.returncode = code
        self.stdout = out
        self.stderr = ""


def test_repair_refuses_reboot(monkeypatch: object) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> _Completed:
        calls.append(list(command))
        text = "scheduled to run at reboot" if "/f" not in command else "clean"
        return _Completed(0, text)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.disk.spawn.elevation_ready",
        lambda _system: True,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.disk.spawn.subprocess.run",
        fake_run,
    )
    plan = repair_plan(
        _volume(), "STICK", HostSystem.WINDOWS, RepairMode.APPLY, busy=False
    )
    assert not isinstance(plan, Failure)
    failure = apply_repair(plan)
    assert isinstance(failure, Failure)
    assert failure.code == "reboot-repair"
    assert calls and "/f" not in calls[0]


def test_repair_runs_after_a_clean_check(monkeypatch: object) -> None:
    def fake_run(_command: list[str], **_kwargs: object) -> _Completed:
        return _Completed(0, "clean")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.disk.spawn.elevation_ready",
        lambda _system: True,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.disk.spawn.subprocess.run",
        fake_run,
    )
    plan = repair_plan(
        _volume(), "STICK", HostSystem.WINDOWS, RepairMode.APPLY, busy=False
    )
    assert not isinstance(plan, Failure)
    assert apply_repair(plan) is None
    text = json.dumps(
        {
            "device_id": "E:",
            "mount_point": "E:\\",
            "label": "STICK",
            "filesystem": "exFAT",
            "size_bytes": 5,
            "removable": True,
            "is_system": False,
            "is_boot": False,
        }
    )
    parsed = parse_volume_json(text)
    assert not isinstance(parsed, Failure)
    assert parsed[0].removable is True
    assert isinstance(parse_volume_json("[]"), tuple)
    assert isinstance(parse_volume_json("{"), Failure)
    lsblk = volumes_from_lsblk(
        json.dumps(
            {
                "blockdevices": [
                    {
                        "path": "/dev/sdb1",
                        "label": "STICK",
                        "fstype": "exfat",
                        "size": 10,
                        "rm": 1,
                        "mountpoint": "/mnt/stick",
                    }
                ]
            }
        )
    )
    assert not isinstance(lsblk, Failure)
    assert lsblk[0].removable is True
    assert isinstance(volumes_from_lsblk("{"), Failure)
    assert windows_probe_command()[0] == "powershell"
    assert "writable" in windows_probe_command()[-1]
    assert linux_probe_command()[0] == "lsblk"
    assert "RO" in linux_probe_command()[-1]
    mounts = parse_mount_writable(
        "/dev/sdb1 /mnt/stick ntfs rw,relatime 0 0\n"
        "/dev/sdb2 /mnt/ro ext4 ro,relatime 0 0\n"
    )
    assert mounts["/mnt/stick"] is True
    assert mounts["/mnt/ro"] is False
    adjusted = apply_mount_writable(lsblk, "/dev/sdb1 /mnt/stick exfat ro 0 0\n")
    assert adjusted[0].writable is False


def test_profile_copy_and_settings(tmp_path: Path) -> None:
    missing = profile_sources(HostSystem.WINDOWS, {})
    assert isinstance(missing, Failure)
    home = tmp_path / "home"
    appdata = tmp_path / "appdata"
    user = appdata / "Cursor" / "User" / "globalStorage"
    user.mkdir(parents=True)
    (user / "state.vscdb").write_text("db", encoding="utf-8")
    (user / "state.vscdb.backup").write_text("bak", encoding="utf-8")
    (appdata / "Cursor" / "Local State").write_text("{}", encoding="utf-8")
    dot = home / ".cursor"
    (dot / "extensions" / "one").mkdir(parents=True)
    (dot / "extensions" / "one" / "ext.js").write_text("x", encoding="utf-8")
    (dot / "skills").mkdir()
    (dot / "skills" / "skill.md").write_text("s", encoding="utf-8")
    (dot / "argv.json").write_text("{}", encoding="utf-8")
    (dot / "mcp.json").write_text("{}", encoding="utf-8")
    sources = profile_sources(
        HostSystem.WINDOWS,
        {"USERPROFILE": str(home), "APPDATA": str(appdata)},
    )
    assert not isinstance(sources, Failure)
    layout = StickLayout.from_mount(tmp_path / "mount")
    report = copy_profile(
        sources, layout, cursor_running=True, include_state_backup=False
    )
    assert report.skipped_chats is True
    assert not (layout.user_data / "User" / "globalStorage" / "state.vscdb").exists()
    assert (layout.dot_cursor / "skills" / "skill.md").is_file()
    mac = profile_sources(HostSystem.MACOS, {"HOME": str(home)})
    assert not isinstance(mac, Failure)
    linux = profile_sources(HostSystem.LINUX, {"HOME": str(home)})
    assert not isinstance(linux, Failure)
    settings = layout.user_data / "User" / "settings.json"
    assert set_interpreter(settings, Path("python")) is True
    settings.write_text("{ /* comment */ }", encoding="utf-8")
    assert set_interpreter(settings, Path("python")) is False


def test_path_home_shortcuts_and_cache(tmp_path: Path) -> None:
    assert prepend_segment("C:\\Windows", "D:\\py", ";") == "D:\\py;C:\\Windows"
    assert prepend_segment("D:\\py;C:\\Windows", "D:\\py", ";") == "D:\\py;C:\\Windows"
    assert remove_segment("D:\\py;C:\\Windows", "D:\\py", ";") == "C:\\Windows"
    block = path_block("/opt/py/bin")
    assert remove_path_block("hello\n" + block) == "hello\n"
    assert remove_path_block("no block") == "no block"
    assert remove_path_block("# >>> cursor-portable >>>\nopen") is None
    updated = insert_path_block("export A=1\n", "/opt/py/bin")
    assert "# >>> cursor-portable >>>" in updated
    both = path_block(("/opt/py/bin", "/opt/py/Scripts"))
    assert both.count("# >>> cursor-portable >>>") == 1
    assert "cursor_portable_bin_0" in both
    assert "cursor_portable_bin_1" in both
    store = MemoryPathStore("C:\\Windows", "expand")
    added = prepend_user_path(store, ("D:\\py", "D:\\py\\Scripts"), ";")
    assert added[0] == "D:\\py"
    assert store.kind == "expand"
    restore_user_path(store, added, ";")
    assert store.value == "C:\\Windows"
    rc = RcPathStore(tmp_path / ".profile")
    assert update_rc_block(rc.path, "/opt/py/bin", remove=False) is True
    assert update_rc_block(rc.path, "/opt/py/bin", remove=True) is True
    rc.path.write_text("# >>> cursor-portable >>>\n", encoding="utf-8")
    assert update_rc_block(rc.path, "/opt/py/bin", remove=True) is False
    profile = tmp_path / "shell-profile"
    profile.write_text("export A=1\n", encoding="utf-8")
    rc_store = RcPathStore(profile)
    added_rc = prepend_user_path(rc_store, ("/opt/py/bin",), ":")
    assert "export A=1" in profile.read_text(encoding="utf-8")
    cleanup_session(
        SessionRecord(path_segments=list(added_rc), path_separator=":"),
        rc_store,
        DirectoryLinker(),
        delete_cache=False,
    )
    restored = profile.read_text(encoding="utf-8")
    assert "export A=1" in restored
    assert "cursor-portable" not in restored
    assert is_our_home_target("E:/CursorPortable/data/dot-cursor") is True
    assert is_our_home_target(
        "C:/Users/Main/AppData/Local/cursor-portable/runtime/app/data/dot-cursor"
    )
    assert is_our_home_target(
        "C:/Users/Main/AppData/Local/cursor-portable/runtime/abc/app/data/dot-cursor"
    )
    link = tmp_path / "dot"
    target = tmp_path / "CursorPortable" / "data" / "dot-cursor"
    target.mkdir(parents=True)
    plan = plan_home_link(
        link, target, current_target=None, link_exists=False, backup_exists=False
    )
    assert plan.action is HomeAction.CREATE
    linker = DirectoryLinker()
    assert apply_home_link(plan, linker) is None
    assert link_target(link) is not None
    again = plan_home_link(
        link,
        target,
        current_target=str(target),
        link_exists=True,
        backup_exists=False,
    )
    assert again.action is HomeAction.ALREADY
    missing = plan_home_link(
        tmp_path / "other",
        tmp_path / "missing-target",
        current_target=None,
        link_exists=False,
        backup_exists=False,
    )
    assert missing.action is HomeAction.MISSING_TARGET
    before = (Shortcut("menu.lnk", "C:\\Program\\Cursor.exe"),)
    after = (Shortcut("menu.lnk", "E:\\CursorPortable\\app\\Cursor.exe"),)
    shortcut_restored = restorations(
        before, after, frozenset({"E:\\CursorPortable\\app\\Cursor.exe"})
    )
    assert shortcut_restored[0].target == "C:\\Program\\Cursor.exe"
    assert is_portable_target(
        "E:/CursorPortable/app/Cursor.exe", ("E:/CursorPortable",)
    )
    assert managed_shortcut("Cursor Portable.lnk") is True
    assert (
        choose_sync(
            stick_version="1", cache_version=None, cache_ready=False, pending_sync=False
        )
        is SyncDirection.STICK_TO_CACHE
    )
    assert (
        choose_sync(
            stick_version="1", cache_version="2", cache_ready=True, pending_sync=True
        )
        is SyncDirection.CACHE_TO_STICK
    )
    assert (
        choose_sync(
            stick_version="2", cache_version="1", cache_ready=True, pending_sync=False
        )
        is SyncDirection.STICK_TO_CACHE
    )
    assert (
        choose_sync(
            stick_version="1", cache_version="1", cache_ready=True, pending_sync=False
        )
        is SyncDirection.NONE
    )
    assert (
        sync_after_exit(launched_version="1", cache_version="2")
        is SyncDirection.CACHE_TO_STICK
    )
    assert (
        sync_after_exit(launched_version="1", cache_version=None) is SyncDirection.NONE
    )
    assert is_cursor_installer_name("Cursor.exe") is False
    assert is_cursor_installer_name("CursorUserSetup.exe") is True
    assert is_cursor_installer_name("inno_updater.exe") is True
    assert installer_pids(
        (ProcessInfo(4, "CursorUserSetup.exe"), ProcessInfo(5, "notepad.exe"))
    ) == (4,)
    assert (
        parse_tasklist_csv('"Image Name","PID"\n"CursorUserSetup.exe","40"\n')[0].pid
        == 40
    )
    assert parse_ps("12 CursorUserSetup.exe")[0].pid == 12
    report = parse_shortcut_report("a.lnk|C:\\Cursor.exe\n")
    assert report[0].location == "a.lnk"
    marker = "x86_64-pc-windows-msvc-install_only.tar.gz"
    assert (
        select_python_asset(
            (
                "cpython-3.14.10+20261026-x86_64-pc-windows-msvc-freethreaded-install_only.tar.gz",
                f"cpython-3.14.9+20261001-{marker}",
                f"cpython-3.14.10+20261026-{marker}",
                f"cpython-3.13.18+20261026-{marker}",
                f"cpython-3.15.2+20261026-{marker}",
            ),
            marker,
        )
        == f"cpython-3.14.10+20261026-{marker}"
    )
    assert select_python_asset(("nope",), "missing") is None
    record = SessionRecord(path_segments=["D:\\py"], pid="9")
    session = tmp_path / "session.txt"
    write_session(session, record)
    assert parse_session(session.read_text(encoding="utf-8")).pid == "9"
    host = default_host_root(tmp_path, tmp_path / "Local", windows=True)
    assert host.name == "cursor-portable"
    shortcut = tmp_path / "Cursor Portable.lnk"
    shortcut.write_text("x", encoding="utf-8")
    assert delete_managed_shortcuts((shortcut,)) == (shortcut,)


def test_fake_winreg(monkeypatch: object) -> None:
    class FakeKey:
        def __init__(self, store: dict[str, tuple[str, int]]) -> None:
            self.store = store

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    class FakeWinreg:
        HKEY_CURRENT_USER = 1
        REG_SZ = 1
        REG_EXPAND_SZ = 2
        KEY_SET_VALUE = 2

        def __init__(self) -> None:
            self.values: dict[str, tuple[str, int]] = {}

        def OpenKey(self, *_args: object) -> FakeKey:
            return FakeKey(self.values)

        def QueryValueEx(self, key: FakeKey, name: str) -> tuple[str, int]:
            if name not in key.store:
                raise FileNotFoundError(name)
            return key.store[name]

        def SetValueEx(
            self, key: FakeKey, name: str, _reserved: int, kind: int, value: str
        ) -> None:
            key.store[name] = (value, kind)

    fake = FakeWinreg()
    monkeypatch.setattr(path_store_module, "_WINREG", fake)  # type: ignore[attr-defined]
    store = WinregPathStore()
    assert store.read() == ("", "plain")
    store.write("%USERPROFILE%\\bin", "expand")
    assert store.read() == ("%USERPROFILE%\\bin", "expand")


def test_install_sfx_and_launch(tmp_path: Path) -> None:
    app = tmp_path / "payload" / "ide-user" / "resources" / "app"
    app.mkdir(parents=True)
    (app / "product.json").write_text(
        '{"version": "1.2.3", "commit": "abc", "target": "system"}',
        encoding="utf-8",
    )
    (app.parent.parent / "Cursor.exe").write_text("exe", encoding="utf-8")
    mount = tmp_path / "mount"
    mount.mkdir()
    layout = StickLayout.from_mount(mount)
    layout.user_data.mkdir(parents=True)
    (layout.user_data / "old.txt").write_text("stale", encoding="utf-8")
    choices = InstallChoices(
        product=ProductKind.IDE,
        scope=InstallScope.USER,
        profile=ProfileMode.FRESH,
        volume_device="E:",
        typed_label="STICK",
    )
    assert scope_dir(tmp_path / "payload", InstallScope.USER).name == "ide-user"
    assert (
        install_tree(
            scope_dir(tmp_path / "payload", InstallScope.USER),
            layout,
            choices,
            HostSystem.WINDOWS,
            sources=None,
            cursor_running=False,
        )
        is None
    )
    assert read_cursor_version(layout.version_file) == "1.2.3"
    assert not (layout.user_data / "old.txt").exists()
    assert layout.windows_launcher.is_file()
    assert not layout.unix_launcher.exists()
    identity = read_stick_identity(layout.version_file)
    assert identity == ("windows", "x64", "NTFS")
    assert "target" not in (
        layout.app / "resources" / "app" / "product.json"
    ).read_text(encoding="utf-8")
    blob = build_windows_sfx(b"SFX", b"7z-bytes")
    assert blob.startswith(b"SFX")
    assert b";!@Install@!UTF-8!" in blob
    assert windows_sfx_config().startswith(";!@Install@!UTF-8!")
    assert "payload" not in windows_sfx_config()
    assert "-m cursor_portable" in windows_sfx_config()
    posix = build_posix_sfx(b"extractor-bytes", b"archive-bytes")
    assert b"payload" not in posix
    assert b'-m cursor_portable "$@"' in posix
    offsets = posix_offsets(posix)
    assert not isinstance(offsets, Failure)
    extractor_offset, extractor_size, archive_offset, archive_size = offsets
    assert (
        posix[extractor_offset : extractor_offset + extractor_size]
        == b"extractor-bytes"
    )
    assert posix[archive_offset : archive_offset + archive_size] == b"archive-bytes"
    assert isinstance(
        extract_command(HostSystem.WINDOWS, tmp_path / "a.exe", tmp_path), list
    )
    assert isinstance(
        extract_command(HostSystem.LINUX, tmp_path / "a.AppImage", tmp_path), list
    )
    assert isinstance(
        extract_command(HostSystem.MACOS, tmp_path / "a.dmg", tmp_path), list
    )
    write_version_file(
        layout.version_file,
        cursor_version="1.2.3",
        commit="abc",
        scope=InstallScope.USER,
        product=ProductKind.IDE,
    )
    cache = tmp_path / "cache"
    (layout.app / "Cursor.exe").write_text("exe", encoding="utf-8")
    home = tmp_path / "home-cursor"
    store = MemoryPathStore("C:\\Windows")
    stopped: list[int] = []

    def run(command: object, on_tick: object) -> int:
        del command
        assert callable(on_tick)
        on_tick()
        return 0

    hooks = LaunchHooks(
        store=store,
        linker=DirectoryLinker(),
        run=run,  # type: ignore[arg-type]
        processes=lambda: (ProcessInfo(9, "CursorUserSetup.exe"),),
        stop=lambda pids: stopped.extend(pids),
        read_shortcuts=lambda: (
            Shortcut(str(tmp_path / "Cursor.lnk"), str(cache / "Cursor.exe")),
        ),
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
    assert code == 0
    assert stopped == [9, 9]
    assert store.value == "C:\\Windows"
    assert python_segments(layout, HostSystem.LINUX)[0].endswith("bin")
    command = build_launch(layout, cache, tmp_path / "scratch", HostSystem.WINDOWS)
    assert "data" in command.argv[1].replace("\\", "/")
    assert command.argv[1].replace("\\", "/").endswith("data/user-data")
    assert "--disk-cache-dir=" in command.argv[3]
    assert ("NODE_NO_WARNINGS", "1") in command.extra_env
    session = SessionRecord(
        path_segments=[str(layout.python)], path_separator=";", pid="1"
    )
    cleanup_session(session, store, DirectoryLinker(), delete_cache=False)


def test_wizard_flow() -> None:
    assert WizardStep.TARGET in step_sequence(HostSystem.WINDOWS)
    assert WizardStep.SCOPE in step_sequence(HostSystem.WINDOWS)
    assert WizardStep.SCOPE not in step_sequence(HostSystem.LINUX)
    assert WizardStep.SCOPE in step_sequence(HostSystem.LINUX, HostSystem.WINDOWS)
    assert WizardStep.PROFILE not in step_sequence(HostSystem.LINUX, HostSystem.WINDOWS)
    assert next_step(HostSystem.LINUX, WizardStep.PRODUCT) is WizardStep.TARGET
    assert next_step(HostSystem.LINUX, WizardStep.TARGET) is WizardStep.PROFILE
    assert next_step(HostSystem.LINUX, WizardStep.CONFIRM) is None
    stick = _volume()
    assert (
        classify_drive(stick, HostSystem.WINDOWS, busy=False) is DriveDisposition.READY
    )
    assert (
        classify_drive(_volume(filesystem="FAT32"), HostSystem.WINDOWS, busy=False)
        is DriveDisposition.NEEDS_FORMAT
    )
    assert isinstance(
        classify_drive(_volume(is_boot=True), HostSystem.WINDOWS, busy=False), Failure
    )
    lines = summary_lines(
        product=ProductKind.BOTH,
        scope=InstallScope.SYSTEM,
        profile=ProfileMode.COPY,
        volume=stick,
        disposition=DriveDisposition.READY,
        target=HostSystem.WINDOWS,
        arch="x64",
    )
    assert any("NTFS" in line for line in lines)


def test_cli_pack_and_repair(tmp_path: Path, capsys: object) -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "does not contain Cursor" in readme
    assert "python -m cursor_portable uninstall" in readme
    assert "install_cursor_portable.cmd" not in readme
    assert main(["--version"]) == 0
    module = tmp_path / "sfx.bin"
    archive = tmp_path / "app.7z"
    module.write_bytes(b"MOD")
    archive.write_bytes(b"ARC")
    output = tmp_path / "setup.exe"
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
    assert output.read_bytes().startswith(b"MOD")
    posix = tmp_path / "setup.run"
    assert (
        main(
            [
                "pack-posix",
                "--extractor",
                str(module),
                "--archive",
                str(archive),
                "--output",
                str(posix),
            ]
        )
        == 0
    )
    assert posix.read_bytes().startswith(b"#!/usr/bin/env bash")
    host = host_system_name()
    native = display_filesystem(filesystem_for(host))
    code = main(
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
    assert code == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    repair_tool = {"windows": "chkdsk", "macos": "diskutil", "linux": "e2fsck"}
    assert repair_tool[host.value] in captured.out
    refused = main(
        [
            "format-volume",
            "--device",
            "C:",
            "--mount",
            "C:\\",
            "--label",
            "OS",
            "--filesystem",
            "NTFS",
            "--typed",
            "OS",
            "--system-disk",
        ]
    )
    assert refused == 1


def test_extract_known_installers(tmp_path: Path, monkeypatch: object) -> None:
    seen: list[list[str]] = []

    def fake_run(command: list[str], cwd: Path | None = None) -> None:
        del cwd
        seen.append(command)
        if command[:2] == ["hdiutil", "attach"]:
            mount = Path(command[command.index("-mountpoint") + 1])
            app = mount / "Cursor.app" / "Contents" / "Resources" / "app"
            app.mkdir(parents=True)
            (app / "product.json").write_text("{}", encoding="utf-8")
        if command[0] == "7z":
            destination = Path(command[2][2:])
            product = destination / "resources" / "app"
            product.mkdir(parents=True, exist_ok=True)
            (product / "product.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.repack.extract_ide.run_command",
        fake_run,
    )
    assert (
        extract_ide(
            HostSystem.WINDOWS,
            tmp_path / "CursorSetup.exe",
            tmp_path / "inno",
            seven_zip="7z",
            innoextract="D:/tools/innoextract.exe",
        )
        is None
    )
    assert seen[0][0] == "D:/tools/innoextract.exe"
    assert (
        extract_ide(
            HostSystem.MACOS,
            tmp_path / "Cursor.dmg",
            tmp_path / "dmg",
            seven_zip="7z",
        )
        is None
    )
    assert (tmp_path / "dmg" / "Cursor.app").is_dir()
    image = tmp_path / "Cursor.AppImage"
    image.write_bytes(b"image")
    assert (
        extract_ide(HostSystem.LINUX, image, tmp_path / "image", seven_zip="7z") is None
    )
    deb = tmp_path / "cursor.deb"
    deb.write_bytes(b"deb")
    assert isinstance(
        extract_ide(HostSystem.WINDOWS, deb, tmp_path / "deb-win", seven_zip="7z"),
        Failure,
    )
    assert extract_ide(HostSystem.LINUX, deb, tmp_path / "deb", seven_zip="7z") is None
    assert isinstance(
        extract_ide(
            HostSystem.LINUX, tmp_path / "notes.bin", tmp_path / "bad", seven_zip="7z"
        ),
        Failure,
    )
    found = application_directory(tmp_path / "image")
    assert found == tmp_path / "image"
    assert isinstance(application_directory(tmp_path / "missing-app"), Failure)


def test_extract_archives(tmp_path: Path, monkeypatch: object) -> None:
    payload = tmp_path / "payload.txt"
    payload.write_text("hi", encoding="utf-8")
    archive = tmp_path / "app.tar.gz"
    with tarfile.open(archive, "w:gz") as tar_handle:
        tar_handle.add(payload, arcname="payload.txt")
    assert (
        extract_ide(HostSystem.LINUX, archive, tmp_path / "tar", seven_zip="7z") is None
    )
    assert (tmp_path / "tar" / "payload.txt").is_file()
    broken = tmp_path / "broken.tar.gz"
    broken.write_bytes(b"not-a-tar")
    assert isinstance(
        extract_ide(HostSystem.LINUX, broken, tmp_path / "broken", seven_zip="7z"),
        Failure,
    )
    packed = tmp_path / "app.zip"
    with zipfile.ZipFile(packed, "w") as packed_zip:
        packed_zip.writestr("readme.txt", "ok")
    assert (
        extract_ide(HostSystem.LINUX, packed, tmp_path / "zip", seven_zip="7z") is None
    )
    unsafe = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as unsafe_zip:
        unsafe_zip.writestr("../evil.txt", "no")
    assert isinstance(
        extract_ide(HostSystem.LINUX, unsafe, tmp_path / "unsafe", seven_zip="7z"),
        Failure,
    )
    image = tmp_path / "Cursor.AppImage"
    image.write_bytes(b"image")

    def fail_seven(_command: list[str], cwd: Path | None = None) -> Failure:
        del cwd
        return Failure("command", "7z failed")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.repack.extract_ide.run_command",
        fail_seven,
    )
    assert isinstance(
        extract_ide(HostSystem.LINUX, image, tmp_path / "fallback", seven_zip="7z"),
        Failure,
    )


def test_bulk_copy_fallback(tmp_path: Path, monkeypatch: object) -> None:
    source = tmp_path / "many"
    source.mkdir()
    for index in range(32):
        (source / f"f{index}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.repack.copy_tree.is_windows",
        lambda: False,
    )
    assert copy_tree(source, tmp_path / "tar-out", skip_names=frozenset()) == 32
    assert (tmp_path / "tar-out" / "f0.txt").is_file()

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise OSError("tar missing")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.repack.copy_tree.subprocess.Popen",
        refuse,
    )
    assert copy_tree(source, tmp_path / "file-out", skip_names=frozenset()) == 32


def test_volume_busy_and_shortcut(tmp_path: Path, monkeypatch: object) -> None:
    mount = tmp_path / "stick"
    mount.mkdir()
    volume = _volume(mount_point=str(mount))
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.host.activity.default_host_root",
        lambda *_args, **_kwargs: tmp_path / "host",
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.host.activity.pid_alive",
        lambda pid: pid == 4,
    )
    assert volume_busy(volume, HostSystem.WINDOWS) is False
    host = tmp_path / "host"
    host.mkdir()
    write_session(host / "session.txt", SessionRecord(stick_root=str(mount), pid="4"))
    assert volume_busy(volume, HostSystem.WINDOWS) is True
    write_session(host / "session.txt", SessionRecord(stick_root=str(mount), pid="no"))
    assert volume_busy(volume, HostSystem.WINDOWS) is False
    write_session(
        host / "session.txt",
        SessionRecord(stick_root=str(tmp_path / "other"), pid="4"),
    )
    assert volume_busy(volume, HostSystem.WINDOWS) is False
    assert cursor_editor_running() in {True, False}
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.launcher.host_watch.Path.home",
        lambda: tmp_path,
    )
    write_managed_shortcut(tmp_path / "cursor-portable", windows=False)
    desktop = tmp_path / ".local" / "share" / "applications" / "cursor-portable.desktop"
    assert "Cursor Portable" in desktop.read_text(encoding="utf-8")
    monkeypatch.delenv("APPDATA", raising=False)
    write_managed_shortcut(tmp_path / "CursorPortable.cmd", windows=True)
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "argv.json").write_text("{}\n", encoding="utf-8")
    sync_profile(profile, tmp_path / "copied")
    assert (tmp_path / "copied" / "argv.json").is_file()
    sync_profile(tmp_path / "absent-profile", tmp_path / "nowhere")
    link = tmp_path / "menu.lnk"
    link.write_bytes(b"original")
    saved = with_snapshots((Shortcut(str(link), "C:\\Cursor.exe"),), tmp_path / "snap")
    assert Path(saved[0].snapshot).is_file()


def test_apply_disk_commands(monkeypatch: object, capsys: object) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.cli.disk_cmd.volume_busy",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.cli.disk_cmd.apply_repair",
        lambda _plan: Failure("reboot-repair", "later"),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.cli.disk_cmd.execute_plan",
        lambda _plan: 0,
    )
    repair_args = argparse.Namespace(
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
        run_disk_command(
            repair_args,
            HostSystem.WINDOWS,
            repair=True,
            probed=(_volume(),),
        )
        == 1
    )
    values = vars(repair_args)
    values["filesystem"] = "FAT32"
    format_args = argparse.Namespace(**values)
    assert (
        run_disk_command(
            format_args,
            HostSystem.WINDOWS,
            repair=False,
            probed=(_volume(filesystem="FAT32"),),
        )
        == 0
    )
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "later" in captured.out
