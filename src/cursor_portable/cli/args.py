"""Argument parser for ``cursor-portable``."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(prog="cursor-portable")
    parser.add_argument("--version", action="store_true")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("wizard")
    launch = commands.add_parser("launch")
    launch.add_argument("--root", required=True)
    commands.add_parser("uninstall-host")
    commands.add_parser("uninstall")
    pack = commands.add_parser("pack-sfx")
    pack.add_argument("--module", required=True)
    pack.add_argument("--archive", required=True)
    pack.add_argument("--output", required=True)
    posix = commands.add_parser("pack-posix")
    posix.add_argument("--extractor", required=True)
    posix.add_argument("--archive", required=True)
    posix.add_argument("--output", required=True)
    repair = commands.add_parser("repair")
    _add_disk_arguments(repair)
    repair.add_argument("--apply", action="store_true")
    fmt = commands.add_parser("format-volume")
    _add_disk_arguments(fmt)
    fmt.add_argument("--apply", action="store_true")
    return parser


def _add_disk_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--device", required=True)
    parser.add_argument("--mount", required=True)
    parser.add_argument("--label", default="")
    parser.add_argument("--filesystem", required=True)
    parser.add_argument("--typed", required=True)
    parser.add_argument("--removable", action="store_true")
    parser.add_argument("--system-disk", action="store_true")
    parser.add_argument("--boot-disk", action="store_true")
    parser.add_argument("--size", type=int, default=0)
    parser.add_argument("--target", default="")
    parser.add_argument("--read-only", action="store_true")
