"""Format and repair commands that print a plan unless ``--apply`` is set."""

from __future__ import annotations

import argparse
import logging

from cursor_portable.disk.apply import apply_repair
from cursor_portable.disk.commands import DiskPlan, RepairMode, format_plan, repair_plan
from cursor_portable.disk.listing import load_probed_volumes
from cursor_portable.disk.spawn import capture_command
from cursor_portable.disk.volume import Volume
from cursor_portable.failure import Failure
from cursor_portable.host.activity import volume_busy
from cursor_portable.models import HostSystem

LOGGER = logging.getLogger(__name__)


def volume_from_args(args: argparse.Namespace) -> Volume:
    """Build a volume from explicit command arguments."""
    return Volume(
        device_id=str(args.device),
        mount_point=str(args.mount),
        label=str(args.label),
        filesystem=str(args.filesystem),
        size_bytes=int(args.size),
        removable=bool(args.removable),
        is_system=bool(args.system_disk),
        is_boot=bool(args.boot_disk),
        writable=not bool(getattr(args, "read_only", False)),
    )


def _probed_volume(
    device_id: str,
    system: HostSystem,
    probed: tuple[Volume, ...] | None,
) -> Volume | Failure:
    volumes = probed if probed is not None else load_probed_volumes(system)
    if isinstance(volumes, Failure):
        return volumes
    for item in volumes:
        if item.device_id.casefold() == device_id.casefold():
            return item
    return Failure("missing-volume", f"{device_id} is not a mounted volume.")


def chosen_target(args: argparse.Namespace, host: HostSystem) -> HostSystem | Failure:
    """Return ``--target`` or the computer running the command."""
    raw = str(getattr(args, "target", "") or "")
    if not raw:
        return host
    try:
        return HostSystem(raw)
    except ValueError:
        return Failure("target", f"Unknown target system: {raw}.")


def run_disk_command(
    args: argparse.Namespace,
    system: HostSystem,
    *,
    repair: bool,
    probed: tuple[Volume, ...] | None = None,
) -> int:
    """Print the plan. Run it only when ``--apply`` is present."""
    volume = volume_from_args(args)
    if args.apply:
        matched = _probed_volume(volume.device_id, system, probed)
        if isinstance(matched, Failure):
            LOGGER.error("%s", matched.message)
            print(matched.message)
            return 1
        volume = matched
    busy = volume_busy(volume, system)
    target = chosen_target(args, system)
    if isinstance(target, Failure):
        LOGGER.error("%s", target.message)
        print(target.message)
        return 1
    if repair:
        plan: DiskPlan | Failure = repair_plan(
            volume,
            str(args.typed),
            system,
            RepairMode.APPLY if args.apply else RepairMode.CHECK,
            busy=busy,
            target=target,
        )
    else:
        plan = format_plan(
            volume,
            str(args.typed),
            system,
            busy=busy,
            target=target,
        )
    if isinstance(plan, Failure):
        LOGGER.error("%s", plan.message)
        print(plan.message)
        return 1
    print(plan.summary)
    print(" ".join(plan.command))
    if not args.apply:
        return 0
    LOGGER.info("Applying disk command: %s", plan.command)
    if repair:
        failure = apply_repair(plan)
        if failure is not None:
            LOGGER.error("%s", failure.message)
            print(failure.message)
            return 1
        return 0
    return execute_plan(plan)


def execute_plan_result(plan: DiskPlan) -> Failure | None:
    """Run a format command. Windows asks for approval when this process is not elevated."""
    captured = capture_command(plan.command, elevate=plan.needs_elevation)
    if isinstance(captured, Failure):
        return captured
    code, output = captured
    if output.strip():
        LOGGER.info("%s", output[-4000:])
    if code != 0:
        detail = output.strip().splitlines()[-1] if output.strip() else ""
        message = f"Disk command exited {code}."
        if detail:
            message = f"{message} {detail}"
        return Failure("disk", message)
    return None


def execute_plan(plan: DiskPlan) -> int:
    """Run a format or repair command that already passed the guards."""
    failure = execute_plan_result(plan)
    if failure is None:
        return 0
    LOGGER.error("%s", failure.message)
    print(failure.message)
    return 1
