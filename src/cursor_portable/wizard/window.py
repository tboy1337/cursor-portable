"""PySide6 wizard. Business rules live in ``wizard.flow``."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging

from PySide6.QtCore import QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from cursor_portable.disk.commands import (
    DiskPlan,
    RepairMode,
    format_plan,
    repair_plan,
)
from cursor_portable.disk.filesystem import display_filesystem, filesystem_for
from cursor_portable.disk.volume import Volume, confirmation_hint, label_matches
from cursor_portable.failure import Failure
from cursor_portable.install.acquire import host_arch
from cursor_portable.models import (
    HostSystem,
    InstallChoices,
    InstallScope,
    ProductKind,
    ProfileMode,
)
from cursor_portable.wizard.flow import (
    DriveDisposition,
    WizardStep,
    classify_drive,
    completion_lines,
    drive_message,
    next_step,
    preferred_drive_index,
    step_sequence,
    summary_lines,
)

LOGGER = logging.getLogger(__name__)


@dataclass
class WizardCallbacks:
    """Actions the window can request. Tests replace these."""

    volumes: Callable[[], tuple[Volume, ...] | Failure]
    busy: Callable[[Volume], bool]
    run_format: Callable[[DiskPlan], Failure | None]
    run_repair: Callable[[DiskPlan], Failure | None]
    install: Callable[[InstallChoices, Volume], Failure | None]
    system: HostSystem
    progress: Callable[[str], None] | None = None
    uninstall: Callable[[], int] | None = None


class ProductPage(QWizardPage):
    """Choose the IDE, the CLI, or both."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Product")
        layout = QVBoxLayout(self)
        self._group = QButtonGroup(self)
        self.ide = QRadioButton("Cursor IDE")
        self.cli = QRadioButton("Cursor CLI")
        self.both = QRadioButton("IDE and CLI")
        self.both.setChecked(True)
        for button in (self.ide, self.cli, self.both):
            self._group.addButton(button)
            layout.addWidget(button)

    def product(self) -> ProductKind:
        """Return the selected product."""
        if self.cli.isChecked():
            return ProductKind.CLI
        if self.both.isChecked():
            return ProductKind.BOTH
        return ProductKind.IDE


def _default_arch() -> str:
    detected = host_arch()
    if isinstance(detected, Failure):
        LOGGER.info("CPU architecture is unknown: %s", detected.message)
        return "x64"
    return detected


class TargetPage(QWizardPage):
    """Choose the system and CPU the stick will run on."""

    def __init__(self, host: HostSystem) -> None:
        super().__init__()
        self.setTitle("Target computer")
        layout = QVBoxLayout(self)
        note = QLabel("The stick is formatted for this system: NTFS, APFS, or ext4.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self._systems = QButtonGroup(self)
        self.windows = QRadioButton("Windows")
        self.macos = QRadioButton("macOS")
        self.linux = QRadioButton("Linux")
        for button in (self.windows, self.macos, self.linux):
            self._systems.addButton(button)
            layout.addWidget(button)
        _system_button(host, self).setChecked(True)
        self._arches = QButtonGroup(self)
        self.x64 = QRadioButton("x64")
        self.arm64 = QRadioButton("ARM64")
        for button in (self.x64, self.arm64):
            self._arches.addButton(button)
            layout.addWidget(button)
        if _default_arch() == "arm64":
            self.arm64.setChecked(True)
            return
        self.x64.setChecked(True)

    def target_os(self) -> HostSystem:
        """Return the selected operating system."""
        if self.macos.isChecked():
            return HostSystem.MACOS
        if self.linux.isChecked():
            return HostSystem.LINUX
        return HostSystem.WINDOWS

    def target_arch(self) -> str:
        """Return ``x64`` or ``arm64``."""
        if self.arm64.isChecked():
            return "arm64"
        return "x64"


def _system_button(host: HostSystem, page: TargetPage) -> QRadioButton:
    if host is HostSystem.MACOS:
        return page.macos
    if host is HostSystem.LINUX:
        return page.linux
    return page.windows


class ScopePage(QWizardPage):
    """Choose the Windows user or system build."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Windows build")
        layout = QVBoxLayout(self)
        self.user = QRadioButton("User build (no admin install)")
        self.system = QRadioButton("System build (admin install)")
        self.user.setChecked(True)
        layout.addWidget(self.user)
        layout.addWidget(self.system)

    def scope(self) -> InstallScope:
        """Return the selected build."""
        if self.system.isChecked():
            return InstallScope.SYSTEM
        return InstallScope.USER


class ProfilePage(QWizardPage):
    """Choose a copy of this computer or an empty profile."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Settings")
        layout = QVBoxLayout(self)
        self.copy = QRadioButton("Copy settings from this computer")
        self.fresh = QRadioButton("Start with an empty profile")
        self.copy.setChecked(True)
        layout.addWidget(self.copy)
        layout.addWidget(self.fresh)

    def profile(self) -> ProfileMode:
        """Return the selected profile mode."""
        if self.fresh.isChecked():
            return ProfileMode.FRESH
        return ProfileMode.COPY


class DrivePage(QWizardPage):
    """Pick a removable drive and type its label before format or repair."""

    def __init__(
        self,
        callbacks: WizardCallbacks,
        target_os: Callable[[], HostSystem],
    ) -> None:
        super().__init__()
        self.setTitle("Removable drive")
        self._callbacks = callbacks
        self._target_os = target_os
        self.volumes: tuple[Volume, ...] = ()
        self.disposition: DriveDisposition | Failure | None = None
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._row_changed)
        self.detail = QLabel("Select a drive.")
        self.detail.setWordWrap(True)
        self.label = QLineEdit()
        self.label.setPlaceholderText("Type the volume label to confirm")
        buttons = QHBoxLayout()
        self.refresh = QPushButton("Refresh")
        self.format_button = QPushButton("Format")
        self.repair_button = QPushButton("Repair")
        self.uninstall_button = QPushButton("Uninstall")
        self.refresh.clicked.connect(self.reload)
        self.format_button.clicked.connect(self._format)
        self.repair_button.clicked.connect(self._repair)
        self.uninstall_button.clicked.connect(self._uninstall)
        buttons.addWidget(self.refresh)
        buttons.addWidget(self.format_button)
        buttons.addWidget(self.repair_button)
        buttons.addWidget(self.uninstall_button)
        layout.addWidget(self.list)
        layout.addWidget(self.detail)
        layout.addWidget(self.label)
        layout.addLayout(buttons)

    def initializePage(self) -> None:  # noqa: N802
        """Load volumes when the page is shown."""
        self.reload()

    def reload(self) -> None:
        """Replace the list with the current volumes."""
        loaded = self._callbacks.volumes()
        self.list.clear()
        if isinstance(loaded, Failure):
            self.volumes = ()
            self.detail.setText(loaded.message)
            return
        self.volumes = loaded
        for volume in self.volumes:
            self.list.addItem(
                f"{volume.mount_point}  {volume.label or '(no label)'}  {volume.filesystem}"
            )
        chosen = preferred_drive_index(
            self.volumes,
            self._callbacks.system,
            self._target_os(),
            self._callbacks.busy,
        )
        if chosen is None:
            self.list.setCurrentRow(-1)
            self._row_changed(-1)
            return
        self.list.setCurrentRow(chosen)

    def selected(self) -> Volume | None:
        """Return the highlighted volume."""
        row = self.list.currentRow()
        if row < 0 or row >= len(self.volumes):
            return None
        return self.volumes[row]

    def _row_changed(self, _row: int) -> None:
        volume = self.selected()
        if volume is None:
            self.detail.setText("Select a drive.")
            self.label.setPlaceholderText("Type the volume label to confirm")
            self.disposition = None
            return
        self.label.setPlaceholderText(confirmation_hint(volume))
        target = self._target_os()
        expected = display_filesystem(filesystem_for(target))
        self.format_button.setText(f"Format as {expected}")
        self.disposition = classify_drive(
            volume,
            self._callbacks.system,
            target,
            busy=self._callbacks.busy(volume),
        )
        if isinstance(self.disposition, Failure):
            self.detail.setText(self.disposition.message)
            return
        self.detail.setText(drive_message(volume, self.disposition, target))

    def validatePage(self) -> bool:  # noqa: N802
        """Block Next until the filesystem matches and the label matches."""
        volume = self.selected()
        if volume is None or not isinstance(self.disposition, DriveDisposition):
            return False
        if self.disposition is not DriveDisposition.READY:
            return False
        return label_matches(volume, self.label.text())

    def _uninstall(self) -> None:
        action = self._callbacks.uninstall
        if action is None:
            QMessageBox.warning(
                self,
                "Cursor Portable",
                "Uninstall is not available.",
            )
            return
        code = action()
        if code == 0:
            QMessageBox.information(
                self,
                "Cursor Portable",
                "Removed the cache, PATH entry, and shortcuts. "
                "The removable drive was not changed.",
            )
            return
        if code == 3:
            QMessageBox.warning(
                self,
                "Cursor Portable",
                "Cursor Portable is still running.",
            )
            return
        QMessageBox.warning(self, "Cursor Portable", f"Uninstall failed ({code}).")

    def _format(self) -> None:
        volume = self.selected()
        if volume is None:
            return
        plan = format_plan(
            volume,
            self.label.text(),
            self._callbacks.system,
            target=self._target_os(),
            busy=self._callbacks.busy(volume),
        )
        self._run_disk(plan, self._callbacks.run_format)

    def _repair(self) -> None:
        volume = self.selected()
        if volume is None:
            return
        plan = repair_plan(
            volume,
            self.label.text(),
            self._callbacks.system,
            RepairMode.APPLY,
            target=self._target_os(),
            busy=self._callbacks.busy(volume),
        )
        self._run_disk(plan, self._callbacks.run_repair)

    def _run_disk(
        self,
        plan: DiskPlan | Failure,
        action: Callable[[DiskPlan], Failure | None],
    ) -> None:
        if isinstance(plan, Failure):
            QMessageBox.warning(self, "Cursor Portable", plan.message)
            return
        answer = QMessageBox.question(
            self,
            "Cursor Portable",
            plan.summary,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        failure = action(plan)
        if failure is not None:
            QMessageBox.warning(self, "Cursor Portable", failure.message)
            return
        self.reload()


def _transfer_prefix(message: str) -> str | None:
    """Return the text before a download size, so later updates replace one line."""
    if " MB" not in message and " KB" not in message:
        return None
    index = next((pos for pos, char in enumerate(message) if char.isdigit()), None)
    if index is None or index == 0:
        return None
    return message[:index]


def same_transfer(previous: str, message: str) -> bool:
    """Return whether both lines are size updates for the same download."""
    for stem in ("Downloading ", "Downloaded "):
        if previous.startswith(stem) and message.startswith(stem):
            left = _transfer_prefix(previous)
            right = _transfer_prefix(message)
            return left is not None and left == right
    return False


class ConfirmPage(QWizardPage):
    """Show the choices, live progress, and a result that stays until Close."""

    def __init__(self, wizard: SetupWizard) -> None:
        super().__init__()
        self.setTitle("Confirm")
        self._wizard = wizard
        self._done = False
        self._lines: list[str] = []
        layout = QVBoxLayout(self)
        self.status = QLabel("Review these choices, then click Finish.")
        self.status.setWordWrap(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(180)
        layout.addWidget(self.status)
        layout.addWidget(self.log)

    def initializePage(self) -> None:  # noqa: N802
        """Refresh the summary from the earlier pages."""
        if self._done:
            return
        self._lines = list(self._wizard.summary())
        self._refresh_log()
        self.status.setText("Review these choices, then click Finish.")

    def validatePage(self) -> bool:  # noqa: N802
        """Copy files when the user presses Finish. Close only after they read the result."""
        if self._done:
            return True
        self._wizard.report_progress(self._show)
        self._show("Preparing the portable install. This can take several minutes.")
        button = self.wizard().button(QWizard.WizardButton.FinishButton)
        if button is not None:
            button.setEnabled(False)
        try:
            failure = self._wizard.perform_install()
        finally:
            if button is not None:
                button.setEnabled(True)
        if failure is not None:
            QMessageBox.warning(self, "Cursor Portable", failure.message)
            return False
        self._mark_ready()
        return False

    def _show(self, message: str) -> None:
        """Append one progress line. Size updates replace the previous size."""
        LOGGER.info("%s", message)
        if self._lines and same_transfer(self._lines[-1], message):
            self._lines[-1] = message
        else:
            self._lines.append(message)
        self.status.setText(message)
        self._refresh_log()
        QApplication.processEvents()

    def _refresh_log(self) -> None:
        self.log.setPlainText("\n".join(self._lines))
        scrollbar = self.log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _mark_ready(self) -> None:
        volume = self._wizard.drive_page.selected()
        mount = volume.mount_point if volume is not None else ""
        for line in completion_lines(mount, self._wizard.target_page.target_os()):
            self._show(line)
        self._done = True
        self.setTitle("Finished")
        wizard = self.wizard()
        wizard.setButtonText(QWizard.WizardButton.FinishButton, "Close")
        back = wizard.button(QWizard.WizardButton.BackButton)
        if back is not None:
            back.setEnabled(False)
        cancel = wizard.button(QWizard.WizardButton.CancelButton)
        if cancel is not None:
            cancel.setEnabled(False)


class SetupWizard(QWizard):
    """Collect choices and call the install callback."""

    def __init__(self, callbacks: WizardCallbacks) -> None:
        super().__init__()
        self.setWindowTitle("Cursor Portable")
        self._callbacks = callbacks
        self._opened_on_drive = False
        self.product_page = ProductPage()
        self.target_page = TargetPage(callbacks.system)
        self.scope_page = ScopePage()
        self.profile_page = ProfilePage()
        self.drive_page = DrivePage(callbacks, self.target_page.target_os)
        self.confirm_page = ConfirmPage(self)
        self._ids = {
            WizardStep.PRODUCT: self.addPage(self.product_page),
            WizardStep.TARGET: self.addPage(self.target_page),
            WizardStep.SCOPE: self.addPage(self.scope_page),
            WizardStep.PROFILE: self.addPage(self.profile_page),
            WizardStep.DRIVE: self.addPage(self.drive_page),
            WizardStep.CONFIRM: self.addPage(self.confirm_page),
        }

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Land on the drive page with the earlier choices still on Back."""
        super().showEvent(event)
        if self._opened_on_drive:
            return
        self._opened_on_drive = True
        QTimer.singleShot(0, self.open_on_drive)

    def open_on_drive(self) -> None:
        """Advance to the drive page, leaving the earlier pages on Back."""
        drive = self._ids[WizardStep.DRIVE]
        for _page in range(8):
            if self.currentId() == drive:
                return
            previous = self.currentId()
            self.next()
            if self.currentId() == previous:
                return

    def nextId(self) -> int:  # noqa: N802
        """Skip pages that do not apply to the selected target."""
        current = self._step_for(self.currentId())
        if current is None:
            return -1
        following = next_step(
            self._callbacks.system,
            current,
            self.target_page.target_os(),
        )
        if following is None:
            return -1
        return self._ids[following]

    def _step_for(self, page_id: int) -> WizardStep | None:
        for step, value in self._ids.items():
            if value == page_id:
                return step
        return None

    def choices(self) -> InstallChoices | None:
        """Return the answers, or None when the drive page is incomplete."""
        volume = self.drive_page.selected()
        if volume is None:
            return None
        target = self.target_page.target_os()
        scope = (
            self.scope_page.scope()
            if target is HostSystem.WINDOWS
            else InstallScope.USER
        )
        profile = (
            self.profile_page.profile()
            if target is self._callbacks.system
            else ProfileMode.FRESH
        )
        return InstallChoices(
            product=self.product_page.product(),
            scope=scope,
            profile=profile,
            volume_device=volume.device_id,
            typed_label=self.drive_page.label.text(),
            target_os=target,
            target_arch=self.target_page.target_arch(),
        )

    def summary(self) -> Sequence[str]:
        """Return the confirmation lines."""
        volume = self.drive_page.selected()
        disposition = self.drive_page.disposition
        if volume is None or not isinstance(disposition, DriveDisposition):
            return ("Choose a removable drive.",)
        target = self.target_page.target_os()
        scope = (
            self.scope_page.scope()
            if target is HostSystem.WINDOWS
            else InstallScope.USER
        )
        profile = (
            self.profile_page.profile()
            if target is self._callbacks.system
            else ProfileMode.FRESH
        )
        return summary_lines(
            product=self.product_page.product(),
            scope=scope,
            profile=profile,
            volume=volume,
            disposition=disposition,
            target=target,
            arch=self.target_page.target_arch(),
        )

    def report_progress(self, callback: Callable[[str], None] | None) -> None:
        """Show acquire status on the confirm page while files download."""
        self._callbacks.progress = callback

    def reject(self) -> None:  # noqa: N802
        """Keep a finished install on screen until Close is clicked."""
        if self.confirm_page._done:
            return
        super().reject()

    def perform_install(self) -> Failure | None:
        """Run the install callback."""
        selected = self.choices()
        volume = self.drive_page.selected()
        if selected is None or volume is None:
            return Failure("incomplete", "Choose a product and a drive.")
        LOGGER.info(
            "Installing %s onto %s",
            selected.product.value,
            volume.mount_point,
        )
        return self._callbacks.install(selected, volume)


def visible_steps(
    system: HostSystem,
    target: HostSystem | None = None,
) -> tuple[WizardStep, ...]:
    """Re-export the page list for tests."""
    return step_sequence(system, target)


def run_wizard(callbacks: WizardCallbacks) -> int:
    """Show the wizard. Returns 0 on accept."""
    app = QApplication.instance() or QApplication([])
    wizard = SetupWizard(callbacks)
    accepted = wizard.exec()
    del app
    if accepted:
        return 0
    return 1
