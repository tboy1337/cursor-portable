"""Run a repair only after a read-only check that will not wait for reboot."""

from __future__ import annotations

from pathlib import Path

from cursor_portable.disk.commands import DiskPlan, refuses_reboot_repair
from cursor_portable.disk.spawn import capture_command
from cursor_portable.failure import Failure


def _windows_fix(plan: DiskPlan) -> bool:
    if not plan.command:
        return False
    name = Path(plan.command[0]).name.casefold()
    flags = {part.casefold() for part in plan.command}
    return name in {"chkdsk", "chkdsk.exe"} and "/f" in flags


def _as_check(plan: DiskPlan) -> DiskPlan:
    args = [part for part in plan.command if part.casefold() != "/f"]
    return DiskPlan(
        command=tuple(args),
        needs_elevation=plan.needs_elevation,
        summary=plan.summary,
    )


def _capture(plan: DiskPlan) -> tuple[int, str] | Failure:
    return capture_command(plan.command, elevate=plan.needs_elevation)


def apply_repair(plan: DiskPlan) -> Failure | None:
    """Run a repair. A Windows fix that wants a reboot is not started."""
    if _windows_fix(plan):
        checked = _capture(_as_check(plan))
        if isinstance(checked, Failure):
            return checked
        code, output = checked
        if code != 0:
            return Failure("disk", f"The read-only check exited {code}.")
        if refuses_reboot_repair(output):
            return Failure(
                "reboot-repair",
                "Windows would postpone this repair until reboot. Nothing was repaired.",
            )
    captured = _capture(plan)
    if isinstance(captured, Failure):
        return captured
    code, output = captured
    if refuses_reboot_repair(output):
        return Failure(
            "reboot-repair",
            "Windows would postpone this repair until reboot. "
            "The volume was left unrepaired.",
        )
    if code != 0:
        return Failure("disk", f"Disk command exited {code}.")
    return None
