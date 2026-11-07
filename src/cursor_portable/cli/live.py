"""Wire the wizard to a payload directory. Disk plans still go through the guards."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from cursor_portable.cli.disk_cmd import execute_plan_result
from cursor_portable.cli.launch_cmd import uninstall_host
from cursor_portable.disk.apply import apply_repair
from cursor_portable.disk.commands import DiskPlan
from cursor_portable.disk.filesystem import display_filesystem, filesystem_for
from cursor_portable.disk.listing import load_probed_volumes
from cursor_portable.disk.volume import Volume, confirmation_hint, label_matches
from cursor_portable.failure import Failure
from cursor_portable.host.activity import cursor_editor_running, volume_busy
from cursor_portable.install.acquire import Progress, acquire_payload, payload_cache
from cursor_portable.install.apply import install_tree, scope_dir
from cursor_portable.models import (
    HostSystem,
    InstallChoices,
    ProfileMode,
    host_system_name,
)
from cursor_portable.profile.sources import profile_sources
from cursor_portable.repack.archive import run_command, seven_zip_extract
from cursor_portable.repack.layout import StickLayout
from cursor_portable.wizard.flow import DriveDisposition, classify_drive
from cursor_portable.wizard.window import WizardCallbacks

LOGGER = logging.getLogger(__name__)


def payload_directory(argv: list[str] | None = None) -> Path | None:
    """Read ``--payload-dir`` or ``--payload``."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--payload-dir")
    parser.add_argument("--payload")
    known, _unused = parser.parse_known_args(argv)
    if known.payload_dir:
        return Path(str(known.payload_dir))
    if known.payload:
        return Path(str(known.payload))
    return None


def bundled_extractor() -> str:
    """Use the extractor shipped beside the wizard, then a system 7-Zip."""
    override = os.environ.get("CURSOR_PORTABLE_7Z")
    if override:
        return override
    for name in ("7zz.exe", "7zz", "7z.exe", "7z"):
        candidate = Path.cwd() / name
        if candidate.is_file():
            return str(candidate)
    return "7z"


def _extract_payload(path: Path) -> Path | Failure:
    if path.is_dir():
        return path
    extractor = bundled_extractor()
    destination = path.parent / f"{path.stem}-extracted"
    failure = run_command(seven_zip_extract(extractor, path, destination))
    if failure is not None:
        return failure
    return destination


def _run_plan(plan: DiskPlan) -> Failure | None:
    return execute_plan_result(plan)


def _refuse_unready(
    choices: InstallChoices,
    volume: Volume,
    host: HostSystem,
) -> Failure | None:
    if not label_matches(volume, choices.typed_label):
        message = (
            "Type the volume label exactly."
            if volume.label.strip()
            else confirmation_hint(volume)
        )
        return Failure("label-mismatch", message)
    disposition = classify_drive(
        volume,
        host,
        choices.target_os,
        busy=volume_busy(volume, host),
    )
    if isinstance(disposition, Failure):
        return disposition
    if disposition is DriveDisposition.READY:
        return None
    expected = display_filesystem(filesystem_for(choices.target_os))
    return Failure(
        "needs-format",
        f"Format the drive as {expected} before installing.",
    )


def _acquire_payload(
    choices: InstallChoices,
    host: HostSystem,
    payload: Path | None,
    progress: Progress | None,
) -> Path | Failure:
    if payload is None:
        payload = payload_cache()
        LOGGER.info("No payload was provided. Downloading into %s", payload)
        if progress is not None:
            progress("Downloading Cursor. This can take several minutes.")
        downloaded = acquire_payload(choices, host, payload, progress=progress)
        if downloaded is not None:
            return downloaded
    return _extract_payload(payload)


def install_payload(
    choices: InstallChoices,
    volume: Volume,
    payload: Path | None,
    *,
    system: HostSystem | None = None,
    progress: Progress | None = None,
) -> Failure | None:
    """Install after the wizard has already checked the label and filesystem."""
    host = host_system_name() if system is None else system
    refused = _refuse_unready(choices, volume, host)
    if refused is not None:
        return refused
    if choices.profile is ProfileMode.COPY and choices.target_os is not host:
        return Failure(
            "profile",
            "Settings from this computer are only copied onto a stick for the same system.",
        )
    opened = _acquire_payload(choices, host, payload, progress)
    if isinstance(opened, Failure):
        return opened
    sources = None
    if choices.profile.value == "copy":
        loaded = profile_sources(host)
        if isinstance(loaded, Failure):
            return loaded
        sources = loaded
    return install_tree(
        scope_dir(opened, choices.scope),
        StickLayout.from_mount(Path(volume.mount_point)),
        choices,
        host,
        sources=sources,
        cursor_running=cursor_editor_running(),
        python_source=opened / "python" if (opened / "python").is_dir() else None,
        cli_source=opened / "cli" if (opened / "cli").is_dir() else None,
        tool_source=opened / "tool" if (opened / "tool").is_dir() else None,
        git_source=opened / "git" if (opened / "git").is_dir() else None,
        progress=progress,
    )


def wizard_callbacks(
    volumes: tuple[Volume, ...] = (),
    payload: Path | None = None,
    system: HostSystem | None = None,
    *,
    probe: bool = False,
) -> WizardCallbacks:
    """Build wizard callbacks. Volumes default to the list the caller supplies."""
    host = host_system_name() if system is None else system

    def listed() -> tuple[Volume, ...] | Failure:
        if volumes:
            return volumes
        if probe:
            return load_probed_volumes(host)
        return ()

    callbacks_ref: list[WizardCallbacks] = []

    def install(choices: InstallChoices, volume: Volume) -> Failure | None:
        current = callbacks_ref[0].progress if callbacks_ref else None
        return install_payload(choices, volume, payload, system=host, progress=current)

    def run_format(plan: DiskPlan) -> Failure | None:
        return _run_plan(plan)

    def run_repair(plan: DiskPlan) -> Failure | None:
        return apply_repair(plan)

    built = WizardCallbacks(
        volumes=listed,
        busy=lambda volume: volume_busy(volume, host),
        run_format=run_format,
        run_repair=run_repair,
        install=install,
        system=host,
        uninstall=uninstall_host,
    )
    callbacks_ref.append(built)
    return built
