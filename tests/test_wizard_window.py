"""Wizard window tests. Qt runs offscreen."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMessageBox, QWizard

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
from cursor_portable.wizard.window import (
    SetupWizard,
    WizardCallbacks,
    run_wizard,
    same_transfer,
    visible_steps,
)


def _volume(filesystem: str = "NTFS") -> Volume:
    return Volume(
        device_id="E:",
        mount_point="E:\\",
        label="STICK",
        filesystem=filesystem,
        size_bytes=10,
        removable=True,
        is_system=False,
        is_boot=False,
    )


def _callbacks(system: HostSystem, filesystem: str = "NTFS") -> WizardCallbacks:
    return WizardCallbacks(
        volumes=lambda: (_volume(filesystem),),
        busy=lambda _volume: False,
        run_format=lambda _plan: None,
        run_repair=lambda _plan: None,
        install=lambda _choices, _volume: None,
        system=system,
    )


def test_linux_wizard_skips_scope(qtbot: object) -> None:
    wizard = SetupWizard(_callbacks(HostSystem.LINUX, "ext4"))
    qtbot.addWidget(wizard)  # type: ignore[attr-defined]
    wizard.show()
    QApplication.processEvents()
    assert wizard.currentId() == wizard._ids[WizardStep.DRIVE]
    assert wizard.product_page.product() is ProductKind.BOTH
    back = wizard.button(QWizard.WizardButton.BackButton)
    assert back is not None
    assert back.isEnabled()
    back.click()
    assert wizard.currentId() == wizard._ids[WizardStep.PROFILE]
    wizard.setCurrentId(wizard._ids[WizardStep.PRODUCT])
    assert wizard.nextId() == wizard._ids[WizardStep.TARGET]
    wizard.setCurrentId(wizard._ids[WizardStep.TARGET])
    assert wizard.nextId() == wizard._ids[WizardStep.PROFILE]
    wizard.target_page.windows.setChecked(True)
    assert wizard.nextId() == wizard._ids[WizardStep.SCOPE]
    wizard.setCurrentId(wizard._ids[WizardStep.SCOPE])
    assert wizard.nextId() == wizard._ids[WizardStep.DRIVE]
    wizard.target_page.linux.setChecked(True)
    wizard.product_page.both.setChecked(True)
    assert wizard.product_page.product() is ProductKind.BOTH
    wizard.profile_page.fresh.setChecked(True)
    wizard.drive_page.reload()
    wizard.drive_page.label.setText("STICK")
    assert wizard.drive_page.validatePage() is True
    summary = wizard.summary()
    assert any("fresh" in line for line in summary)
    assert wizard.choices() is not None
    assert wizard.choices().scope is InstallScope.USER  # type: ignore[union-attr]
    assert wizard.choices().target_os is HostSystem.LINUX  # type: ignore[union-attr]


def test_drive_format_repair_and_confirm(qtbot: object, monkeypatch: object) -> None:
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

    def question(*_args: object) -> QMessageBox.StandardButton:
        answers["count"] += 1
        if answers["count"] == 1:
            return QMessageBox.StandardButton.No
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(QMessageBox, "question", question)  # type: ignore[attr-defined]
    state = {"volumes": (fat, _volume()), "busy": False}

    def listed() -> tuple[Volume, ...] | Failure:
        return state["volumes"]  # type: ignore[return-value]

    callbacks = WizardCallbacks(
        volumes=listed,
        busy=lambda _volume: bool(state["busy"]),
        run_format=lambda _plan: None,
        run_repair=lambda _plan: Failure("repair", "stopped"),
        install=lambda _choices, _volume: None,
        system=HostSystem.WINDOWS,
    )
    wizard = SetupWizard(callbacks)
    qtbot.addWidget(wizard)  # type: ignore[attr-defined]
    wizard.show()
    wizard.product_page.cli.setChecked(True)
    assert wizard.product_page.product() is ProductKind.CLI
    wizard.product_page.ide.setChecked(True)
    assert wizard.product_page.product() is ProductKind.IDE
    wizard.scope_page.system.setChecked(True)
    assert wizard.scope_page.scope() is InstallScope.SYSTEM
    wizard.profile_page.copy.setChecked(True)
    assert wizard.profile_page.profile() is ProfileMode.COPY
    wizard.drive_page.initializePage()
    wizard.drive_page.list.setCurrentRow(0)
    wizard.drive_page.label.setText("STICK")
    wizard.drive_page._format()
    wizard.drive_page._format()
    wizard.drive_page.list.setCurrentRow(1)
    wizard.drive_page._format()
    wizard.drive_page._repair()
    state["busy"] = True
    wizard.drive_page._row_changed(0)
    assert wizard.drive_page.validatePage() is False
    state["busy"] = False
    callbacks.volumes = lambda: Failure("volumes", "probe failed")
    wizard.drive_page.reload()
    assert "probe failed" in wizard.drive_page.detail.text()
    state["volumes"] = ()
    callbacks.volumes = listed
    wizard.drive_page.reload()
    assert wizard.drive_page.selected() is None
    wizard.drive_page._format()
    wizard.drive_page._repair()
    assert wizard.drive_page.validatePage() is False
    assert wizard.choices() is None
    assert wizard.summary() == ("Choose a removable drive.",)
    assert wizard.perform_install() is not None
    wizard.confirm_page.initializePage()
    assert wizard.confirm_page.validatePage() is False
    state["volumes"] = (_volume(),)
    wizard.drive_page.reload()
    wizard.drive_page.label.setText("STICK")
    wizard.drive_page._row_changed(0)
    wizard.confirm_page.initializePage()
    assert wizard.confirm_page.validatePage() is False
    assert "Cursor Portable is ready." in wizard.confirm_page.log.toPlainText()
    assert wizard.confirm_page.validatePage() is True
    wizard.setCurrentId(wizard._ids[WizardStep.CONFIRM])
    assert wizard.nextId() == -1
    assert next_step(HostSystem.LINUX, WizardStep.SCOPE) is WizardStep.PRODUCT
    assert default_scope(HostSystem.MACOS) is InstallScope.USER
    assert WizardStep.SCOPE in visible_steps(HostSystem.WINDOWS)


def test_unlabeled_drive_asks_for_the_drive_letter(
    qtbot: object, monkeypatch: object
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
    formatted: list[object] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    callbacks = WizardCallbacks(
        volumes=lambda: (blank,),
        busy=lambda _volume: False,
        run_format=lambda plan: formatted.append(plan) or None,
        run_repair=lambda _plan: None,
        install=lambda _choices, _volume: None,
        system=HostSystem.WINDOWS,
    )
    wizard = SetupWizard(callbacks)
    qtbot.addWidget(wizard)  # type: ignore[attr-defined]
    wizard.drive_page.initializePage()
    detail = wizard.drive_page.detail.text()
    placeholder = wizard.drive_page.label.placeholderText()
    assert "Type E: to confirm" in detail
    assert "Type E: to confirm" in placeholder
    wizard.drive_page.label.setText("E:\\")
    assert wizard.drive_page.validatePage() is False
    wizard.drive_page._format()
    assert formatted


def test_only_usable_drive_is_selected(qtbot: object) -> None:
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
    wizard = SetupWizard(callbacks)
    qtbot.addWidget(wizard)  # type: ignore[attr-defined]
    wizard.drive_page.reload()
    assert wizard.drive_page.selected() == stick
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
    qtbot: object, monkeypatch: object
) -> None:
    seen: dict[str, int] = {}

    def uninstall() -> int:
        seen["code"] = 0
        return 0

    callbacks = _callbacks(HostSystem.WINDOWS)
    callbacks.uninstall = uninstall
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)  # type: ignore[attr-defined]
    wizard = SetupWizard(callbacks)
    qtbot.addWidget(wizard)  # type: ignore[attr-defined]
    wizard.drive_page._uninstall()
    assert seen["code"] == 0
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)  # type: ignore[attr-defined]
    callbacks.uninstall = None
    wizard.drive_page._uninstall()
    callbacks.uninstall = lambda: 3
    wizard.drive_page._uninstall()
    callbacks.uninstall = lambda: 1
    wizard.drive_page._uninstall()


def test_run_wizard_cancel(monkeypatch: object) -> None:
    monkeypatch.setattr(SetupWizard, "exec", lambda _self: 0)  # type: ignore[attr-defined]
    assert run_wizard(_callbacks(HostSystem.WINDOWS)) == 1
    monkeypatch.setattr(SetupWizard, "exec", lambda _self: 1)  # type: ignore[attr-defined]
    assert run_wizard(_callbacks(HostSystem.WINDOWS)) == 0


def test_progress_log_keeps_stages_and_waits_for_close(qtbot: object) -> None:
    calls = {"install": 0}

    def install(_choices: object, _volume: object) -> None:
        calls["install"] += 1
        progress = callbacks.progress
        assert progress is not None
        progress("Downloading the Cursor CLI: 8 MB of 86 MB")
        progress("Downloading the Cursor CLI: 16 MB of 86 MB")
        progress(
            "Copying Cursor IDE onto E:\\CursorPortable. This can take several minutes."
        )
        progress("Copied 3 Cursor IDE files onto E:\\CursorPortable.")

    callbacks = _callbacks(HostSystem.WINDOWS)
    callbacks.install = install
    wizard = SetupWizard(callbacks)
    qtbot.addWidget(wizard)  # type: ignore[attr-defined]
    wizard.show()
    wizard.drive_page.label.setText("STICK")
    wizard.confirm_page.initializePage()
    assert wizard.confirm_page.validatePage() is False
    text = wizard.confirm_page.log.toPlainText()
    assert "Downloading the Cursor CLI: 8 MB" not in text
    assert "Downloading the Cursor CLI: 16 MB of 86 MB" in text
    assert "Copying Cursor IDE onto E:\\CursorPortable" in text
    assert "Cursor Portable is ready." in text
    assert "CursorPortable.cmd" in text
    assert "Click Close when you have read this." in wizard.confirm_page.status.text()
    finish = wizard.button(QWizard.WizardButton.FinishButton)
    assert finish is not None
    assert finish.text() == "Close"
    wizard.reject()
    assert wizard.isVisible()
    assert wizard.confirm_page.validatePage() is True
    assert calls["install"] == 1
    assert same_transfer(
        "Downloading the Cursor CLI: 8 MB of 86 MB",
        "Downloading the Cursor CLI: 16 MB of 86 MB",
    )
    assert not same_transfer(
        "Copying Cursor IDE onto E:\\",
        "Copied 3 Cursor IDE files onto E:\\.",
    )
    assert not same_transfer("Downloading now", "Downloading later")
    assert not same_transfer("Downloading the file: MB", "Downloading the file: MB")
    windows = completion_lines("E:\\", HostSystem.WINDOWS)
    assert any("CursorPortable.cmd" in line for line in windows)
    linux = completion_lines("/mnt/stick", HostSystem.LINUX)
    assert any("cursor-portable" in line for line in linux)
