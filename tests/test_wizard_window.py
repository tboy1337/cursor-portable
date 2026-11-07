"""Wizard window tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import os
from pathlib import Path
import sys

import pytest

from cursor_portable.disk.commands import DiskPlan
from cursor_portable.disk.volume import Volume
from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem, InstallScope, ProductKind, ProfileMode
from cursor_portable.wizard.flow import (
    WizardStep,
    completion_lines,
    default_scope,
    next_step,
    preferred_drive_index,
)
from cursor_portable.wizard import tcl_path, window as wizard_window
from cursor_portable.wizard.window import (
    SetupWizard,
    WizardCallbacks,
    run_wizard,
    same_transfer,
)


def _volume(filesystem: str = "NTFS", device: str = "E:") -> Volume:
    return Volume(
        device_id=device,
        mount_point=f"{device}\\" if device.endswith(":") else device,
        label="STICK",
        filesystem=filesystem,
        size_bytes=10,
        removable=True,
        is_system=False,
        is_boot=False,
    )


def _at(wizard: SetupWizard, step: WizardStep) -> bool:
    """Return whether the wizard shows ``step``.

    Comparing ``current_step`` with ``is`` in the test itself makes mypy
    treat that page as permanent, so later assertions look unreachable.
    """
    return wizard.current_step is step


def _callbacks(system: HostSystem, filesystem: str = "NTFS") -> WizardCallbacks:
    return WizardCallbacks(
        volumes=lambda: (_volume(filesystem),),
        busy=lambda _volume: False,
        run_format=lambda _plan: None,
        run_repair=lambda _plan: None,
        install=lambda _choices, _volume: None,
        system=system,
    )


@pytest.fixture
def make_wizard() -> Iterator[Callable[[WizardCallbacks], SetupWizard]]:
    created: list[SetupWizard] = []

    def build(callbacks: WizardCallbacks) -> SetupWizard:
        wizard = SetupWizard(callbacks)
        created.append(wizard)
        return wizard

    yield build
    for wizard in created:
        if wizard_window._window_open(wizard):
            wizard.destroy()


def test_linux_wizard_skips_scope(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
) -> None:
    wizard = make_wizard(_callbacks(HostSystem.LINUX, "ext4"))
    wizard.open_on_drive()
    assert _at(wizard, WizardStep.DRIVE)
    assert wizard.product_page.product() is ProductKind.BOTH
    assert str(wizard.back_button.cget("state")) == "normal"
    wizard.back_button.invoke()
    assert _at(wizard, WizardStep.PROFILE)
    wizard.open_on_drive()
    assert _at(wizard, WizardStep.PROFILE)
    wizard.show_step(WizardStep.PRODUCT)
    assert str(wizard.back_button.cget("state")) == "disabled"
    wizard.go_back()
    assert _at(wizard, WizardStep.PRODUCT)
    assert wizard.following_step() is WizardStep.TARGET
    wizard.go_next()
    assert _at(wizard, WizardStep.TARGET)
    assert wizard.following_step() is WizardStep.PROFILE
    wizard.target_page.windows.invoke()
    assert wizard.following_step() is WizardStep.SCOPE
    wizard.show_step(WizardStep.SCOPE)
    assert wizard.following_step() is WizardStep.DRIVE
    wizard.target_page.linux.invoke()
    wizard.show_step(WizardStep.SCOPE)
    assert wizard.previous_step() is None
    assert wizard.following_step() is WizardStep.PRODUCT
    wizard.product_page.both.invoke()
    assert wizard.product_page.product() is ProductKind.BOTH
    wizard.profile_page.fresh.invoke()
    wizard.drive_page.reload()
    wizard.drive_page.set_confirmation("STICK")
    assert wizard.drive_page.format_button.cget("text") == "Format as ext4"
    assert wizard.drive_page.ready() is True
    summary = wizard.summary()
    assert any("fresh" in line for line in summary)
    choices = wizard.choices()
    assert choices is not None
    assert choices.scope is InstallScope.USER
    assert choices.target_os is HostSystem.LINUX
    wizard.show_step(WizardStep.CONFIRM)
    wizard.open_on_drive()
    assert _at(wizard, WizardStep.CONFIRM)
    assert wizard.following_step() is None


def test_other_target_starts_fresh(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
) -> None:
    wizard = make_wizard(_callbacks(HostSystem.WINDOWS))
    wizard.open_on_drive()
    wizard.scope_page.system.invoke()
    wizard.profile_page.copy.invoke()
    wizard.target_page.macos.invoke()
    wizard.target_page.arm64.invoke()
    wizard.drive_page.refresh_selection()
    assert wizard.drive_page.ready() is False
    assert "APFS" in wizard.drive_page.detail_text()
    choices = wizard.choices()
    assert choices is not None
    assert choices.scope is InstallScope.USER
    assert choices.profile is ProfileMode.FRESH
    assert choices.target_os is HostSystem.MACOS
    assert choices.target_arch == "arm64"
    assert wizard.summary() == ("Choose a removable drive.",)
    wizard.go_next()
    assert _at(wizard, WizardStep.DRIVE)
    wizard.go_back()
    assert _at(wizard, WizardStep.TARGET)
    wizard.drive_page.set_confirmation("STICK")
    wizard.show_step(WizardStep.DRIVE)
    wizard.go_next()
    assert _at(wizard, WizardStep.DRIVE)


def test_unknown_cpu_defaults_to_x64(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wizard_window,
        "host_arch",
        lambda: Failure("arch", "unknown"),
    )
    wizard = make_wizard(_callbacks(HostSystem.WINDOWS))
    assert wizard.target_page.target_arch() == "x64"
    wizard.target_page.x64.invoke()
    assert wizard.target_page.target_os() is HostSystem.WINDOWS


def test_arm64_host_is_selected(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wizard_window, "host_arch", lambda: "arm64")
    wizard = make_wizard(_callbacks(HostSystem.WINDOWS))
    assert wizard.target_page.target_arch() == "arm64"


def test_drive_format_repair_and_confirm(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fat = Volume(
        device_id="F:",
        mount_point="F:\\",
        label="STICK",
        filesystem="FAT32",
        size_bytes=10,
        removable=True,
        is_system=False,
        is_boot=False,
    )
    answers = {"count": 0}
    plans: list[DiskPlan] = []

    def question(*_args: object, **_kwargs: object) -> bool:
        answers["count"] += 1
        return answers["count"] != 1

    monkeypatch.setattr(wizard_window.messagebox, "showwarning", lambda *_a, **_k: None)
    monkeypatch.setattr(wizard_window.messagebox, "askyesno", question)
    state: dict[str, tuple[Volume, ...] | bool] = {
        "volumes": (fat, _volume()),
        "busy": False,
    }

    def listed() -> tuple[Volume, ...] | Failure:
        volumes = state["volumes"]
        assert isinstance(volumes, tuple)
        return volumes

    callbacks = WizardCallbacks(
        volumes=listed,
        busy=lambda _volume: bool(state["busy"]),
        run_format=lambda plan: plans.append(plan) or None,
        run_repair=lambda _plan: Failure("repair", "stopped"),
        install=lambda _choices, _volume: None,
        system=HostSystem.WINDOWS,
    )
    wizard = make_wizard(callbacks)
    wizard.product_page.cli.invoke()
    assert wizard.product_page.product() is ProductKind.CLI
    wizard.product_page.ide.invoke()
    assert wizard.product_page.product() is ProductKind.IDE
    wizard.scope_page.system.invoke()
    assert wizard.scope_page.scope() is InstallScope.SYSTEM
    wizard.profile_page.copy.invoke()
    assert wizard.profile_page.profile() is ProfileMode.COPY
    wizard.drive_page.on_show()
    wizard.drive_page.select_row(0)
    wizard.drive_page.set_confirmation("STICK")
    wizard.drive_page.format_drive()
    wizard.drive_page.format_drive()
    assert len(plans) == 1
    wizard.drive_page.select_row(1)
    assert wizard.drive_page.format_button.cget("text") == "Format as NTFS"
    wizard.drive_page.format_drive()
    wizard.drive_page.repair_drive()
    assert answers["count"] == 3
    state["busy"] = True
    wizard.drive_page.refresh_selection()
    assert wizard.drive_page.ready() is False
    state["busy"] = False
    callbacks.volumes = lambda: Failure("volumes", "probe failed")
    wizard.drive_page.reload()
    assert "probe failed" in wizard.drive_page.detail_text()
    state["volumes"] = ()
    callbacks.volumes = listed
    wizard.drive_page.reload()
    wizard.drive_page.select_row(5)
    assert wizard.drive_page.selected() is None
    wizard.drive_page.format_drive()
    wizard.drive_page.repair_drive()
    assert wizard.drive_page.ready() is False
    assert wizard.choices() is None
    assert wizard.summary() == ("Choose a removable drive.",)
    assert isinstance(wizard.perform_install(), Failure)
    wizard.confirm_page.on_show()
    assert wizard.confirm_page.finish() is False
    state["volumes"] = (_volume(),)
    wizard.drive_page.reload()
    wizard.drive_page.set_confirmation("STICK")
    wizard.drive_page.refresh_selection()
    wizard.confirm_page.on_show()
    assert wizard.confirm_page.finish() is False
    assert "Cursor Portable is ready." in wizard.confirm_page.log_text()
    assert wizard.confirm_page.finish() is True
    wizard.show_step(WizardStep.CONFIRM)
    assert wizard.following_step() is None
    assert next_step(HostSystem.LINUX, WizardStep.SCOPE) is WizardStep.PRODUCT
    assert default_scope(HostSystem.MACOS) is InstallScope.USER
    assert WizardStep.SCOPE in wizard_window.visible_steps(HostSystem.WINDOWS)


def test_unlabeled_drive_asks_for_the_drive_letter(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blank = Volume(
        device_id="E:",
        mount_point="E:\\",
        label="",
        filesystem="FAT32",
        size_bytes=10,
        removable=True,
        is_system=False,
        is_boot=False,
    )
    formatted: list[DiskPlan] = []

    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unexpected warning")

    monkeypatch.setattr(wizard_window.messagebox, "showwarning", fail)
    monkeypatch.setattr(
        wizard_window.messagebox,
        "askyesno",
        lambda *_args, **_kwargs: True,
    )
    callbacks = WizardCallbacks(
        volumes=lambda: (blank,),
        busy=lambda _volume: False,
        run_format=lambda plan: formatted.append(plan) or None,
        run_repair=lambda _plan: None,
        install=lambda _choices, _volume: None,
        system=HostSystem.WINDOWS,
    )
    wizard = make_wizard(callbacks)
    wizard.drive_page.on_show()
    detail = wizard.drive_page.detail_text()
    hint = wizard.drive_page.hint_text()
    assert "(no label)" in wizard.drive_page.drives.get(0)
    assert "Type E: to confirm" in detail
    assert "Type E: to confirm" in hint
    wizard.drive_page.set_confirmation("E:\\")
    assert wizard.drive_page.confirmation_text() == "E:\\"
    assert wizard.drive_page.ready() is False
    wizard.drive_page.format_drive()
    assert formatted


def test_only_usable_drive_is_selected(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
) -> None:
    blocked = Volume(
        device_id="C:",
        mount_point="C:\\",
        label="System",
        filesystem="NTFS",
        size_bytes=10,
        removable=False,
        is_system=True,
        is_boot=True,
    )
    stick = _volume()
    callbacks = WizardCallbacks(
        volumes=lambda: (blocked, stick),
        busy=lambda _volume: False,
        run_format=lambda _plan: None,
        run_repair=lambda _plan: None,
        install=lambda _choices, _volume: None,
        system=HostSystem.WINDOWS,
    )
    wizard = make_wizard(callbacks)
    wizard.drive_page.reload()
    assert wizard.drive_page.selected() == stick
    wizard.drive_page.refresh_button.invoke()
    assert wizard.drive_page.selected() == stick
    wizard.drive_page.drives.insert("end", "extra")
    wizard.drive_page.select_row(wizard.drive_page.drives.size() - 1)
    assert wizard.drive_page.selected() is None
    wizard.drive_page.drives.event_generate("<<ListboxSelect>>")
    assert wizard.drive_page.selected() is None
    assert (
        preferred_drive_index(
            (stick, _volume("NTFS")),
            HostSystem.WINDOWS,
            HostSystem.WINDOWS,
            lambda _volume: False,
        )
        is None
    )


def test_uninstall_button_reports_host_cleanup(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info: list[str] = []
    warnings: list[str] = []

    def showinfo(_title: str, message: str, **_kwargs: object) -> None:
        info.append(message)

    def showwarning(_title: str, message: str, **_kwargs: object) -> None:
        warnings.append(message)

    monkeypatch.setattr(wizard_window.messagebox, "showinfo", showinfo)
    monkeypatch.setattr(wizard_window.messagebox, "showwarning", showwarning)
    seen: dict[str, int] = {}

    def uninstall() -> int:
        seen["code"] = 0
        return 0

    callbacks = _callbacks(HostSystem.WINDOWS)
    callbacks.uninstall = uninstall
    wizard = make_wizard(callbacks)
    wizard.drive_page.uninstall_host()
    assert seen["code"] == 0
    assert "removable drive was not changed" in info[0]
    callbacks.uninstall = None
    wizard.drive_page.uninstall_host()
    callbacks.uninstall = lambda: 3
    wizard.drive_page.uninstall_host()
    callbacks.uninstall = lambda: 1
    wizard.drive_page.uninstall_host()
    assert warnings == [
        "Uninstall is not available.",
        "Cursor Portable is still running.",
        "Uninstall failed (1).",
    ]


def test_run_wizard_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SetupWizard, "run", lambda _self: 0)
    assert run_wizard(_callbacks(HostSystem.WINDOWS)) == 1
    monkeypatch.setattr(SetupWizard, "run", lambda _self: 1)
    assert run_wizard(_callbacks(HostSystem.WINDOWS)) == 0


def test_run_skips_the_loop_when_the_window_is_already_gone(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wizard = make_wizard(_callbacks(HostSystem.WINDOWS))

    def gone(_widget: object) -> bool:
        return False

    def fail() -> None:
        raise AssertionError("mainloop should not start")

    monkeypatch.setattr(wizard_window, "_window_open", gone)
    monkeypatch.setattr(wizard, "mainloop", fail)
    assert wizard.run() == 0
    wizard.destroy()


def test_run_wizard_accepts_a_closed_window(monkeypatch: pytest.MonkeyPatch) -> None:
    def close(wizard: SetupWizard) -> int:
        wizard.destroy()
        return 1

    monkeypatch.setattr(SetupWizard, "run", close)
    assert run_wizard(_callbacks(HostSystem.WINDOWS)) == 0


def test_open_walks_until_the_page_limit(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wizard = make_wizard(_callbacks(HostSystem.WINDOWS))

    def spin(*_args: object, **_kwargs: object) -> None:
        if wizard.current_step is WizardStep.PRODUCT:
            wizard.show_step(WizardStep.TARGET)
            return
        wizard.show_step(WizardStep.PRODUCT)

    monkeypatch.setattr(wizard, "go_next", spin)
    wizard.open_on_drive()
    assert wizard.current_step in {WizardStep.PRODUCT, WizardStep.TARGET}


def test_run_shows_the_drive_page(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wizard = make_wizard(_callbacks(HostSystem.WINDOWS))

    def stop() -> None:
        wizard.cancel()

    monkeypatch.setattr(wizard, "mainloop", stop)
    assert wizard.run() == 0
    assert _at(wizard, WizardStep.DRIVE)


def test_install_error_reenables_finish(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
) -> None:
    callbacks = _callbacks(HostSystem.WINDOWS)

    def install(_choices: object, _volume: object) -> None:
        raise RuntimeError("boom")

    callbacks.install = install
    wizard = make_wizard(callbacks)
    wizard.drive_page.reload()
    wizard.drive_page.set_confirmation("STICK")
    with pytest.raises(RuntimeError, match="boom"):
        wizard.confirm_page.finish()
    assert str(wizard.finish_button.cget("state")) == "normal"
    assert wizard.confirm_page.finished is False


def test_progress_log_keeps_stages_and_waits_for_close(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
) -> None:
    calls = {"install": 0}
    callbacks = _callbacks(HostSystem.WINDOWS)
    wizard_holder: dict[str, SetupWizard] = {}

    def install(_choices: object, _volume: object) -> None:
        calls["install"] += 1
        wizard = wizard_holder["wizard"]
        assert str(wizard.finish_button.cget("state")) == "disabled"
        progress = callbacks.progress
        assert progress is not None
        progress("Downloading the Cursor CLI: 8 MB of 86 MB")
        progress("Downloading the Cursor CLI: 16 MB of 86 MB")
        progress(
            "Copying Cursor IDE onto E:\\CursorPortable. This can take several minutes."
        )
        progress("Copied 3 Cursor IDE files onto E:\\CursorPortable.")

    callbacks.install = install
    wizard = make_wizard(callbacks)
    wizard_holder["wizard"] = wizard
    wizard.drive_page.reload()
    wizard.drive_page.set_confirmation("STICK")
    wizard.confirm_page.on_show()
    wizard.finish_button.invoke()
    assert wizard.confirm_page.finished is True
    assert wizard_window._window_open(wizard)
    text = wizard.confirm_page.log_text()
    assert "Preparing the portable install" in text
    assert "Downloading the Cursor CLI: 8 MB" not in text
    assert "Downloading the Cursor CLI: 16 MB of 86 MB" in text
    assert "Copying Cursor IDE onto E:\\CursorPortable" in text
    assert "Cursor Portable is ready." in text
    assert "CursorPortable.cmd" in text
    assert wizard.confirm_page.status_text() == "Click Close when you have read this."
    assert wizard.finish_button.cget("text") == "Close"
    wizard.confirm_page.on_show()
    assert "Cursor Portable is ready." in wizard.confirm_page.log_text()
    step = wizard.current_step
    wizard.go_back()
    assert wizard.current_step is step
    wizard.cancel()
    assert wizard_window._window_open(wizard)
    assert wizard.confirm_page.finish() is True
    assert calls["install"] == 1
    wizard.finish_button.invoke()
    assert not wizard_window._window_open(wizard)


def test_open_stops_when_next_cannot_advance(
    make_wizard: Callable[[WizardCallbacks], SetupWizard],
) -> None:
    wizard = make_wizard(_callbacks(HostSystem.WINDOWS))
    wizard.show_step(WizardStep.CONFIRM)
    wizard.open_on_drive()
    assert _at(wizard, WizardStep.CONFIRM)


def test_transfer_lines_and_step_list() -> None:
    assert same_transfer(
        "Downloading the Cursor CLI: 8 MB of 86 MB",
        "Downloading the Cursor CLI: 16 MB of 86 MB",
    )
    assert same_transfer(
        "Downloaded the Cursor CLI: 1 KB of 2 KB",
        "Downloaded the Cursor CLI: 2 KB of 2 KB",
    )
    assert not same_transfer(
        "Downloading the Cursor CLI: 8 MB of 86 MB",
        "Downloaded the Cursor CLI: 8 MB of 86 MB",
    )
    assert not same_transfer(
        "Copying Cursor IDE onto E:\\",
        "Copied 3 Cursor IDE files onto E:\\.",
    )
    assert not same_transfer("Downloading now", "Downloading later")
    assert not same_transfer("Downloading the file: MB", "Downloading the file: MB")
    assert wizard_window._transfer_prefix("8 MB of 9 MB") is None
    assert wizard_window._transfer_prefix("no digits MB") is None
    assert wizard_window._transfer_prefix("plain") is None
    windows = completion_lines("E:\\", HostSystem.WINDOWS)
    assert any("CursorPortable.cmd" in line for line in windows)
    linux = completion_lines("/mnt/stick", HostSystem.LINUX)
    assert any("cursor-portable" in line for line in linux)


def test_posix_tcl_paths_point_at_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = tmp_path / "lib"
    older = lib / "tcl8.6"
    newer = lib / "tcl8.10"
    tk_dir = lib / "tk8.6"
    lettered = lib / "tcl8a"
    for directory, marker in (
        (older, "init.tcl"),
        (newer, "init.tcl"),
        (lettered, "init.tcl"),
        (tk_dir, "tk.tcl"),
    ):
        directory.mkdir(parents=True)
        (directory / marker).write_text("", encoding="utf-8")
    monkeypatch.delenv("TCL_LIBRARY", raising=False)
    monkeypatch.delenv("TK_LIBRARY", raising=False)
    tcl_path.apply_tcl_paths(tmp_path, "linux")
    assert os.environ["TCL_LIBRARY"] == str(newer)
    assert os.environ["TK_LIBRARY"] == str(tk_dir)
    monkeypatch.setenv("TCL_LIBRARY", "kept")
    tcl_path.apply_tcl_paths(tmp_path, "linux")
    assert os.environ["TCL_LIBRARY"] == "kept"
    monkeypatch.delenv("TK_LIBRARY", raising=False)
    tcl_path.apply_tcl_paths(tmp_path / "missing", "linux")
    assert "TK_LIBRARY" not in os.environ
    monkeypatch.setenv("TCL_LIBRARY", "kept")
    tcl_path.apply_tcl_paths(tmp_path, "win32")
    assert os.environ["TCL_LIBRARY"] == "kept"


def test_prepare_tkinter_uses_this_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def record(root: Path, platform: str) -> None:
        seen["root"] = str(root)
        seen["platform"] = platform

    monkeypatch.setattr(tcl_path, "apply_tcl_paths", record)
    tcl_path.prepare_tkinter()
    assert seen["platform"] == sys.platform
    assert Path(seen["root"]).is_dir()
