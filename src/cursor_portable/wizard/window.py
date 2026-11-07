"""Tkinter wizard. Business rules live in ``wizard.flow``."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import gc
import logging
import tkinter as tk
from tkinter import messagebox
from typing import Literal

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
_TITLE = "Cursor Portable"
_HEADINGS = {
    WizardStep.PRODUCT: "Product",
    WizardStep.TARGET: "Target computer",
    WizardStep.SCOPE: "Windows build",
    WizardStep.PROFILE: "Settings",
    WizardStep.DRIVE: "Removable drive",
    WizardStep.CONFIRM: "Confirm",
}


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


def _widget_state(disabled: bool) -> Literal["normal", "disabled"]:
    if disabled:
        return "disabled"
    return "normal"


def _selected_row(box: tk.Listbox) -> int | None:
    """Return the highlighted row.

    The installed tkinter stubs leave ``Listbox.curselection`` untyped.
    """
    selected: object = box.curselection()  # type: ignore[no-untyped-call]
    if not isinstance(selected, tuple) or not selected:
        return None
    row = selected[0]
    if not isinstance(row, int):
        return None
    return row


def _window_open(widget: tk.Misc) -> bool:
    """Return whether this Tk application still exists.

    Tk 9 raises ``TclError`` from ``winfo`` after the root is destroyed.
    """
    try:
        return bool(widget.winfo_exists())
    except tk.TclError:
        return False


def _option(
    parent: tk.Misc,
    variable: tk.StringVar,
    label: str,
    value: str,
) -> tk.Radiobutton:
    button = tk.Radiobutton(
        parent,
        text=label,
        variable=variable,
        value=value,
        anchor=tk.W,
    )
    button.pack(fill=tk.X, anchor=tk.W, pady=2)
    return button


def _warn(parent: tk.Misc, text: str) -> None:
    LOGGER.warning("%s", text)
    messagebox.showwarning(_TITLE, text, parent=parent)


def _inform(parent: tk.Misc, text: str) -> None:
    LOGGER.info("%s", text)
    messagebox.showinfo(_TITLE, text, parent=parent)


def _confirm(parent: tk.Misc, text: str) -> bool:
    LOGGER.info("Asking to confirm: %s", text)
    return bool(messagebox.askyesno(_TITLE, text, parent=parent))


def _default_arch() -> str:
    detected = host_arch()
    if isinstance(detected, Failure):
        LOGGER.info("CPU architecture is unknown: %s", detected.message)
        return "x64"
    return detected


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


def _volume_line(volume: Volume) -> str:
    label = volume.label or "(no label)"
    return f"{volume.mount_point}  {label}  {volume.filesystem}"


class ProductPage(tk.Frame):
    """Choose the IDE, the CLI, or both."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self._choice = tk.StringVar(self, ProductKind.BOTH.value)
        self.ide = _option(self, self._choice, "Cursor IDE", ProductKind.IDE.value)
        self.cli = _option(self, self._choice, "Cursor CLI", ProductKind.CLI.value)
        self.both = _option(self, self._choice, "IDE and CLI", ProductKind.BOTH.value)

    def product(self) -> ProductKind:
        """Return the selected product."""
        return ProductKind(self._choice.get())


class TargetPage(tk.Frame):
    """Choose the system and CPU the stick will run on."""

    def __init__(self, master: tk.Misc, host: HostSystem) -> None:
        super().__init__(master)
        note = tk.Label(
            self,
            text="The stick is formatted for this system: NTFS, APFS, or ext4.",
            wraplength=480,
            justify=tk.LEFT,
        )
        note.pack(anchor=tk.W, pady=(0, 8))
        self._system = tk.StringVar(self, host.value)
        self.windows = _option(self, self._system, "Windows", HostSystem.WINDOWS.value)
        self.macos = _option(self, self._system, "macOS", HostSystem.MACOS.value)
        self.linux = _option(self, self._system, "Linux", HostSystem.LINUX.value)
        self._arch = tk.StringVar(self, _default_arch())
        self.x64 = _option(self, self._arch, "x64", "x64")
        self.arm64 = _option(self, self._arch, "ARM64", "arm64")

    def target_os(self) -> HostSystem:
        """Return the selected operating system."""
        return HostSystem(self._system.get())

    def target_arch(self) -> str:
        """Return ``x64`` or ``arm64``."""
        return self._arch.get()


class ScopePage(tk.Frame):
    """Choose the Windows user or system build."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self._choice = tk.StringVar(self, InstallScope.USER.value)
        self.user = _option(
            self,
            self._choice,
            "User build (no admin install)",
            InstallScope.USER.value,
        )
        self.system = _option(
            self,
            self._choice,
            "System build (admin install)",
            InstallScope.SYSTEM.value,
        )

    def scope(self) -> InstallScope:
        """Return the selected build."""
        return InstallScope(self._choice.get())


class ProfilePage(tk.Frame):
    """Choose a copy of this computer or an empty profile."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self._choice = tk.StringVar(self, ProfileMode.COPY.value)
        self.copy = _option(
            self,
            self._choice,
            "Copy settings from this computer",
            ProfileMode.COPY.value,
        )
        self.fresh = _option(
            self,
            self._choice,
            "Start with an empty profile",
            ProfileMode.FRESH.value,
        )

    def profile(self) -> ProfileMode:
        """Return the selected profile mode."""
        return ProfileMode(self._choice.get())


class DrivePage(tk.Frame):
    """Pick a removable drive and type its label before format or repair."""

    def __init__(
        self,
        master: tk.Misc,
        callbacks: WizardCallbacks,
        target_os: Callable[[], HostSystem],
    ) -> None:
        super().__init__(master)
        self._callbacks = callbacks
        self._target_os = target_os
        self.volumes: tuple[Volume, ...] = ()
        self.disposition: DriveDisposition | Failure | None = None
        self.drives = tk.Listbox(self, height=6, exportselection=False)
        self.drives.pack(fill=tk.BOTH, expand=True, pady=4)
        self.drives.bind("<<ListboxSelect>>", self._on_list_select)
        self.detail = tk.Label(
            self,
            text="Select a drive.",
            wraplength=480,
            justify=tk.LEFT,
        )
        self.detail.pack(anchor=tk.W, pady=4)
        self.hint = tk.Label(
            self,
            text="Type the volume label to confirm",
            wraplength=480,
            justify=tk.LEFT,
        )
        self.hint.pack(anchor=tk.W)
        self.confirmation = tk.Entry(self)
        self.confirmation.pack(fill=tk.X, pady=4)
        actions = tk.Frame(self)
        actions.pack(anchor=tk.W, pady=4)
        self.refresh_button = tk.Button(actions, text="Refresh", command=self.reload)
        self.format_button = tk.Button(
            actions, text="Format", command=self.format_drive
        )
        self.repair_button = tk.Button(
            actions, text="Repair", command=self.repair_drive
        )
        self.uninstall_button = tk.Button(
            actions,
            text="Uninstall",
            command=self.uninstall_host,
        )
        buttons = (
            self.refresh_button,
            self.format_button,
            self.repair_button,
            self.uninstall_button,
        )
        for button in buttons:
            button.pack(side=tk.LEFT, padx=(0, 6))

    def on_show(self) -> None:
        """Load volumes when the page is shown."""
        self.reload()

    def reload(self) -> None:
        """Replace the list with the current volumes."""
        loaded = self._callbacks.volumes()
        self.drives.delete(0, tk.END)
        if isinstance(loaded, Failure):
            self._show_probe_failure(loaded)
            return
        self.volumes = loaded
        LOGGER.info("Found %s volume(s)", len(self.volumes))
        for volume in self.volumes:
            self.drives.insert(tk.END, _volume_line(volume))
        chosen = preferred_drive_index(
            self.volumes,
            self._callbacks.system,
            self._target_os(),
            self._callbacks.busy,
        )
        if chosen is None:
            self.select_row(-1)
            return
        self.select_row(chosen)

    def select_row(self, row: int) -> None:
        """Highlight one row, or clear the highlight when ``row`` is negative."""
        self.drives.selection_clear(0, tk.END)
        if 0 <= row < self.drives.size():
            self.drives.selection_set(row)
            self.drives.activate(row)
            self.drives.see(row)
        self.refresh_selection()

    def selected(self) -> Volume | None:
        """Return the highlighted volume."""
        row = _selected_row(self.drives)
        if row is None or row >= len(self.volumes):
            return None
        return self.volumes[row]

    def refresh_selection(self) -> None:
        """Update the detail text for the highlighted volume."""
        self._apply_selection(self.selected())

    def confirmation_text(self) -> str:
        """Return the typed confirmation."""
        return self.confirmation.get()

    def set_confirmation(self, text: str) -> None:
        """Replace the typed confirmation."""
        self.confirmation.delete(0, tk.END)
        self.confirmation.insert(0, text)
        LOGGER.info("Confirmation field updated")

    def detail_text(self) -> str:
        """Return the drive explanation."""
        return str(self.detail.cget("text"))

    def hint_text(self) -> str:
        """Return the confirmation prompt."""
        return str(self.hint.cget("text"))

    def ready(self) -> bool:
        """Return whether Next can leave this page."""
        volume = self.selected()
        if volume is None or not isinstance(self.disposition, DriveDisposition):
            return False
        if self.disposition is not DriveDisposition.READY:
            return False
        return label_matches(volume, self.confirmation_text())

    def format_drive(self) -> None:
        """Ask, then format the highlighted volume."""
        volume = self.selected()
        if volume is None:
            LOGGER.info("Format skipped because no drive is selected")
            return
        plan = format_plan(
            volume,
            self.confirmation_text(),
            self._callbacks.system,
            target=self._target_os(),
            busy=self._callbacks.busy(volume),
        )
        self._run_disk(plan, self._callbacks.run_format)

    def repair_drive(self) -> None:
        """Ask, then repair the highlighted volume."""
        volume = self.selected()
        if volume is None:
            LOGGER.info("Repair skipped because no drive is selected")
            return
        plan = repair_plan(
            volume,
            self.confirmation_text(),
            self._callbacks.system,
            RepairMode.APPLY,
            target=self._target_os(),
            busy=self._callbacks.busy(volume),
        )
        self._run_disk(plan, self._callbacks.run_repair)

    def uninstall_host(self) -> None:
        """Remove the host cache, PATH entry, and shortcuts."""
        action = self._callbacks.uninstall
        if action is None:
            _warn(self, "Uninstall is not available.")
            return
        code = action()
        LOGGER.info("Uninstall returned %s", code)
        if code == 0:
            _inform(
                self,
                "Removed the cache, PATH entry, and shortcuts. "
                "The removable drive was not changed.",
            )
            return
        if code == 3:
            _warn(self, "Cursor Portable is still running.")
            return
        _warn(self, f"Uninstall failed ({code}).")

    def _show_probe_failure(self, failure: Failure) -> None:
        self.volumes = ()
        self.disposition = None
        self.detail.configure(text=failure.message)
        self._set_hint("Type the volume label to confirm")
        LOGGER.warning("Volume probe failed: %s", failure.message)

    def _on_list_select(self, _event: object) -> None:
        self.refresh_selection()

    def _apply_selection(self, volume: Volume | None) -> None:
        if volume is None:
            self._show_empty()
            return
        self._show_volume(volume)

    def _show_empty(self) -> None:
        self.detail.configure(text="Select a drive.")
        self._set_hint("Type the volume label to confirm")
        self.disposition = None
        LOGGER.info("No removable drive is selected")

    def _show_volume(self, volume: Volume) -> None:
        target = self._target_os()
        self._set_hint(confirmation_hint(volume))
        expected = display_filesystem(filesystem_for(target))
        self.format_button.configure(text=f"Format as {expected}")
        self.disposition = classify_drive(
            volume,
            self._callbacks.system,
            target,
            busy=self._callbacks.busy(volume),
        )
        if isinstance(self.disposition, Failure):
            self.detail.configure(text=self.disposition.message)
            LOGGER.info(
                "Drive %s refused: %s", volume.mount_point, self.disposition.message
            )
            return
        self.detail.configure(text=drive_message(volume, self.disposition, target))
        LOGGER.info("Selected %s (%s)", volume.mount_point, self.disposition.value)

    def _set_hint(self, text: str) -> None:
        self.hint.configure(text=text)

    def _run_disk(
        self,
        plan: DiskPlan | Failure,
        action: Callable[[DiskPlan], Failure | None],
    ) -> None:
        if isinstance(plan, Failure):
            _warn(self, plan.message)
            return
        if not _confirm(self, plan.summary):
            LOGGER.info("Disk action declined")
            return
        failure = action(plan)
        if failure is not None:
            _warn(self, failure.message)
            return
        LOGGER.info("Disk action finished")
        self.reload()


class ConfirmPage(tk.Frame):
    """Show the choices, live progress, and a result that stays until Close."""

    def __init__(self, master: tk.Misc, wizard: SetupWizard) -> None:
        super().__init__(master)
        self._wizard = wizard
        self._done = False
        self._lines: list[str] = []
        self.status = tk.Label(self, text="", wraplength=480, justify=tk.LEFT)
        self.status.pack(anchor=tk.W, pady=(0, 6))
        holder = tk.Frame(self)
        holder.pack(fill=tk.BOTH, expand=True)
        self.log = tk.Text(holder, height=12, wrap=tk.WORD, state=tk.DISABLED)
        scroll = tk.Scrollbar(holder, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    @property
    def finished(self) -> bool:
        """Return whether the install result is waiting for Close."""
        return self._done

    def on_show(self) -> None:
        """Refresh the summary from the earlier pages."""
        if self._done:
            return
        self._lines = list(self._wizard.summary())
        self._refresh_log()
        self.status.configure(text="Review these choices, then click Finish.")

    def finish(self) -> bool:
        """Copy files. A later call returns True once the result is on screen."""
        if self._done:
            return True
        self._wizard.report_progress(self._show)
        self._show("Preparing the portable install. This can take several minutes.")
        self._wizard.finish_button.configure(state=tk.DISABLED)
        try:
            failure = self._wizard.perform_install()
        finally:
            self._wizard.finish_button.configure(state=tk.NORMAL)
        if failure is not None:
            _warn(self, failure.message)
            return False
        self._mark_ready()
        return False

    def log_text(self) -> str:
        """Return the progress log."""
        return self.log.get("1.0", "end-1c")

    def status_text(self) -> str:
        """Return the status line."""
        return str(self.status.cget("text"))

    def _show(self, message: str) -> None:
        """Append one progress line. Size updates replace the previous size."""
        LOGGER.info("%s", message)
        if self._lines and same_transfer(self._lines[-1], message):
            self._lines[-1] = message
        else:
            self._lines.append(message)
        self.status.configure(text=message)
        self._refresh_log()
        self.update_idletasks()

    def _refresh_log(self) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.insert("1.0", "\n".join(self._lines))
        self.log.configure(state=tk.DISABLED)
        self.log.see(tk.END)

    def _mark_ready(self) -> None:
        volume = self._wizard.drive_page.selected()
        mount = volume.mount_point if volume is not None else ""
        for line in completion_lines(mount, self._wizard.target_page.target_os()):
            self._show(line)
        self._done = True
        self._wizard.show_finished()


class _WizardButtons(tk.Frame):
    """Back, Next, Finish, and Cancel."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_back: Callable[[], None],
        on_next: Callable[[], None],
        on_finish: Callable[[], None],
        on_cancel: Callable[[], None],
    ) -> None:
        super().__init__(master)
        self.back = tk.Button(self, text="Back", width=12, command=on_back)
        self.next = tk.Button(self, text="Next", width=12, command=on_next)
        self.finish = tk.Button(self, text="Finish", width=12, command=on_finish)
        self.cancel = tk.Button(self, text="Cancel", width=12, command=on_cancel)
        buttons = (self.back, self.next, self.finish, self.cancel)
        for column, button in enumerate(buttons):
            button.grid(row=0, column=column, padx=4, pady=4)


class SetupWizard(tk.Tk):
    """Collect choices and call the install callback."""

    def __init__(self, callbacks: WizardCallbacks) -> None:
        # Destroying one root and opening another can fail while the first
        # interpreter still holds the Tk library files. Collect it and retry.
        try:
            super().__init__()
        except tk.TclError as exc:
            LOGGER.warning("Tk failed to start (%s); retrying", exc)
            gc.collect()
            super().__init__()
        self.withdraw()
        self.title(_TITLE)
        self.minsize(560, 460)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._callbacks = callbacks
        self._opened_on_drive = False
        self._accepted = 0
        self._step = WizardStep.PRODUCT
        self._heading = tk.Label(self, font=("TkDefaultFont", 14, "bold"))
        self._deck = tk.Frame(self)
        self.product_page = ProductPage(self._deck)
        self.target_page = TargetPage(self._deck, callbacks.system)
        self.scope_page = ScopePage(self._deck)
        self.profile_page = ProfilePage(self._deck)
        self.drive_page = DrivePage(self._deck, callbacks, self.target_page.target_os)
        self.confirm_page = ConfirmPage(self._deck, self)
        self._buttons = _WizardButtons(
            self,
            on_back=self.go_back,
            on_next=self.go_next,
            on_finish=self._on_finish,
            on_cancel=self.cancel,
        )
        self._layout()
        self.show_step(WizardStep.PRODUCT)

    def show_window(self) -> None:
        """Show the window and land on the drive page the first time."""
        self.open_on_drive()
        self.deiconify()
        self.update()

    def open_on_drive(self) -> None:
        """Advance to the drive page, leaving the earlier pages on Back."""
        if self._opened_on_drive:
            return
        self._opened_on_drive = True
        for _page in range(8):
            if self.current_step is WizardStep.DRIVE:
                LOGGER.info("Wizard opened on the drive page")
                return
            previous = self.current_step
            self.go_next(require_ready=False)
            if self.current_step is previous:
                LOGGER.info("Stopped before the drive page at %s", previous.value)
                return

    def show_step(self, step: WizardStep) -> None:
        """Show one page without following Next."""
        LOGGER.info("Showing wizard page %s", step.value)
        self._step = step
        self._frame(step).tkraise()
        self._heading.configure(text=_HEADINGS[step])
        self._enter_step(step)
        self._sync_buttons()

    def go_next(self, *, require_ready: bool = True) -> None:
        """Advance. The drive page stays put until its label matches."""
        if self._drive_blocks_next(require_ready):
            LOGGER.info("Staying on the drive page until it is ready")
            return
        following = self.following_step()
        if following is None:
            LOGGER.info("No page follows %s", self.current_step.value)
            return
        self.show_step(following)

    def go_back(self) -> None:
        """Return to the previous applicable page."""
        if self.confirm_page.finished:
            LOGGER.info("Back is disabled after a finished install")
            return
        previous = self.previous_step()
        if previous is None:
            LOGGER.info("No page precedes %s", self.current_step.value)
            return
        self.show_step(previous)

    def following_step(self) -> WizardStep | None:
        """Return the page Next would open."""
        return next_step(
            self._callbacks.system,
            self.current_step,
            self.target_page.target_os(),
        )

    def previous_step(self) -> WizardStep | None:
        """Return the page Back would open."""
        pages = step_sequence(self._callbacks.system, self.target_page.target_os())
        try:
            index = pages.index(self.current_step)
        except ValueError:
            LOGGER.info(
                "Current page %s is not in the active sequence",
                self.current_step.value,
            )
            return None
        if index == 0:
            return None
        return pages[index - 1]

    def choices(self) -> InstallChoices | None:
        """Return the answers, or None when no drive is selected."""
        volume = self.drive_page.selected()
        if volume is None:
            return None
        scope, profile = self._scope_and_profile()
        return InstallChoices(
            product=self.product_page.product(),
            scope=scope,
            profile=profile,
            volume_device=volume.device_id,
            typed_label=self.drive_page.confirmation_text(),
            target_os=self.target_page.target_os(),
            target_arch=self.target_page.target_arch(),
        )

    def summary(self) -> Sequence[str]:
        """Return the confirmation lines."""
        volume = self.drive_page.selected()
        disposition = self.drive_page.disposition
        if volume is None or not isinstance(disposition, DriveDisposition):
            return ("Choose a removable drive.",)
        scope, profile = self._scope_and_profile()
        return summary_lines(
            product=self.product_page.product(),
            scope=scope,
            profile=profile,
            volume=volume,
            disposition=disposition,
            target=self.target_page.target_os(),
            arch=self.target_page.target_arch(),
        )

    def report_progress(self, callback: Callable[[str], None] | None) -> None:
        """Show acquire status on the confirm page while files download."""
        self._callbacks.progress = callback

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

    def show_finished(self) -> None:
        """Keep the result on screen until Close."""
        self._heading.configure(text="Finished")
        self.finish_button.configure(text="Close")
        self.back_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.DISABLED)
        LOGGER.info("Install finished; waiting for Close")

    def cancel(self) -> None:
        """Close the window. A finished install stays until Close."""
        if self.confirm_page.finished:
            LOGGER.info("Ignoring cancel after a finished install")
            return
        LOGGER.info("Wizard cancelled")
        self._accepted = 0
        self.destroy()

    def run(self) -> int:
        """Show the wizard and return 1 when Close ends a finished install."""
        LOGGER.info("Opening the setup wizard")
        self.show_window()
        if _window_open(self):
            self.mainloop()
        return self._accepted

    @property
    def current_step(self) -> WizardStep:
        """Return the page on screen."""
        return self._step

    @property
    def back_button(self) -> tk.Button:
        """Return the Back button."""
        return self._buttons.back

    @property
    def next_button(self) -> tk.Button:
        """Return the Next button."""
        return self._buttons.next

    @property
    def finish_button(self) -> tk.Button:
        """Return the Finish button."""
        return self._buttons.finish

    @property
    def cancel_button(self) -> tk.Button:
        """Return the Cancel button."""
        return self._buttons.cancel

    def _layout(self) -> None:
        self._heading.grid(row=0, column=0, sticky=tk.W, padx=12, pady=(12, 4))
        self._deck.grid(row=1, column=0, sticky=tk.NSEW, padx=12, pady=4)
        self._buttons.grid(row=2, column=0, sticky=tk.E, padx=12, pady=12)
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self._deck.rowconfigure(0, weight=1)
        self._deck.columnconfigure(0, weight=1)
        for step in WizardStep:
            self._frame(step).grid(row=0, column=0, sticky=tk.NSEW)

    def _enter_step(self, step: WizardStep) -> None:
        if step is WizardStep.DRIVE:
            self.drive_page.on_show()
            return
        if step is WizardStep.CONFIRM:
            self.confirm_page.on_show()

    def _drive_blocks_next(self, require_ready: bool) -> bool:
        return (
            require_ready
            and self.current_step is WizardStep.DRIVE
            and not self.drive_page.ready()
        )

    def _sync_buttons(self) -> None:
        finished = self.confirm_page.finished
        self.back_button.configure(
            state=_widget_state(finished or self.previous_step() is None)
        )
        self.cancel_button.configure(state=_widget_state(finished))
        if self.current_step is WizardStep.CONFIRM:
            self.next_button.grid_remove()
            self.finish_button.grid()
            return
        self.finish_button.grid_remove()
        self.next_button.grid()

    def _scope_and_profile(self) -> tuple[InstallScope, ProfileMode]:
        target = self.target_page.target_os()
        scope = InstallScope.USER
        if target is HostSystem.WINDOWS:
            scope = self.scope_page.scope()
        profile = ProfileMode.FRESH
        if target is self._callbacks.system:
            profile = self.profile_page.profile()
        LOGGER.info("Using scope %s and profile %s", scope.value, profile.value)
        return scope, profile

    def _frame(self, step: WizardStep) -> tk.Frame:
        frames: dict[WizardStep, tk.Frame] = {
            WizardStep.PRODUCT: self.product_page,
            WizardStep.TARGET: self.target_page,
            WizardStep.SCOPE: self.scope_page,
            WizardStep.PROFILE: self.profile_page,
            WizardStep.DRIVE: self.drive_page,
            WizardStep.CONFIRM: self.confirm_page,
        }
        return frames[step]

    def _on_finish(self) -> None:
        if self.confirm_page.finished:
            LOGGER.info("Closing the finished wizard")
            self._accepted = 1
            self.destroy()
            return
        self.confirm_page.finish()


def visible_steps(
    system: HostSystem,
    target: HostSystem | None = None,
) -> tuple[WizardStep, ...]:
    """Re-export the page list for tests."""
    return step_sequence(system, target)


def run_wizard(callbacks: WizardCallbacks) -> int:
    """Show the wizard. Returns 0 when Close ends a finished install."""
    wizard = SetupWizard(callbacks)
    try:
        accepted = wizard.run()
    finally:
        if _window_open(wizard):
            wizard.destroy()
        gc.collect()
    if accepted:
        return 0
    return 1
