"""Command line interface."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import subprocess
import sys
from typing import Sequence

from cursor_portable._version import __version__
from cursor_portable.cli.args import build_parser
from cursor_portable.cli.disk_cmd import run_disk_command
from cursor_portable.cli.launch_cmd import launch_root, uninstall_host
from cursor_portable.launcher.headless import notify_headless
from cursor_portable.logging_config import configure_cli_logging
from cursor_portable.models import HostSystem, host_system_name
from cursor_portable.repack.sfx import build_posix_sfx, build_windows_sfx

LOGGER = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested command. With no command, open the wizard."""
    configure_cli_logging()
    parser = build_parser()
    args = parser.parse_args(None if argv is None else list(argv))
    if args.version and args.command is None:
        print(__version__)
        return 0
    command = args.command or "wizard"
    if command == "wizard":
        return _spawn_wizard(argv)
    if command == "pack-sfx":
        return _pack_windows(args)
    if command == "pack-posix":
        return _pack_posix(args)
    if command == "repair":
        return run_disk_command(args, _system(), repair=True)
    if command == "format-volume":
        return run_disk_command(args, _system(), repair=False)
    if command == "launch":
        return _launch(Path(str(args.root)))
    if command in {"uninstall-host", "uninstall"}:
        return _uninstall_host()
    parser.error(f"Unknown command: {command}")


def _system() -> HostSystem:
    try:
        return host_system_name()
    except ValueError:
        return HostSystem.LINUX


def _spawn_wizard(argv: Sequence[str] | None) -> int:
    extra = [] if argv is None else [arg for arg in argv if arg != "wizard"]
    completed = subprocess.run(
        [sys.executable, "-m", "cursor_portable.wizard", *extra],
        check=False,
    )
    return completed.returncode


def _pack_windows(args: argparse.Namespace) -> int:
    module = Path(str(args.module)).read_bytes()
    archive = Path(str(args.archive)).read_bytes()
    Path(str(args.output)).write_bytes(build_windows_sfx(module, archive))
    return 0


def _pack_posix(args: argparse.Namespace) -> int:
    extractor = Path(str(args.extractor)).read_bytes()
    archive = Path(str(args.archive)).read_bytes()
    Path(str(args.output)).write_bytes(build_posix_sfx(extractor, archive))
    return 0


def _launch(root: Path) -> int:
    try:
        return launch_root(root)
    except Exception as exc:
        LOGGER.exception("Cursor Portable could not start")
        notify_headless(f"Cursor Portable could not start. {exc}")
        return 1


def _uninstall_host() -> int:
    return uninstall_host()
