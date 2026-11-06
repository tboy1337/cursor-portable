#!/usr/bin/env python3
"""Write a self-extractor that contains this tool, not Cursor."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import tarfile
import tempfile

from cursor_portable._version import __version__, write_version_stamp
from cursor_portable.download.catalog import setup_asset_name
from cursor_portable.download.fetch import download_file
from cursor_portable.download.http_client import UrllibClient
from cursor_portable.download.sum_file import expected_sha256, render_sha256sums
from cursor_portable.download.verify_file import check_download, file_sha256
from cursor_portable.failure import Failure
from cursor_portable.install.acquire import innoextract_executable
from cursor_portable.models import HostSystem
from cursor_portable.python_runtime.pin import (
    ASSET_MARKERS,
    PYTHON_MINOR,
    select_python_asset,
)
from cursor_portable.repack.archive import run_command, seven_zip_add
from cursor_portable.repack.sfx import build_posix_sfx, build_windows_sfx

PYTHON_RELEASE = (
    "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
)
SFX_SDK = "https://github.com/ip7z/7zip/releases/download/24.09/lzma2409.7z"
SFX_SHA256 = "79b39f10b7b69eea293caa90c3e7ea07faf8f01f8ae9db1bb1b90c092375e5f3"
# An x64 Windows runner cannot execute the arm64 interpreter it packs.
_CROSS_PLATFORM = {("windows", "arm64"): "aarch64-pc-windows-msvc"}
LOGGER = logging.getLogger(__name__)
PYSIDE_REQUIREMENT = "pyside6==6.12.0"

_HOST = {
    "windows": HostSystem.WINDOWS,
    "macos": HostSystem.MACOS,
    "linux": HostSystem.LINUX,
}
_SEVEN_ZIP_NAMES = ("7zz", "7z", "7zz.exe", "7z.exe")


def asset_names(document: object) -> tuple[str, ...]:
    """Return release asset names from a GitHub release document."""
    if not isinstance(document, dict):
        return ()
    assets = document.get("assets")
    if not isinstance(assets, list):
        return ()
    names: list[str] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str):
            names.append(name)
    return tuple(names)


def asset_url(document: object, name: str) -> str | None:
    """Return the download URL for one release asset."""
    if not isinstance(document, dict):
        return None
    assets = document.get("assets")
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") == name:
            url = asset.get("browser_download_url")
            if isinstance(url, str):
                return url
    return None


def asset_sha256(document: object, name: str) -> str | None:
    """Return a GitHub asset digest when the release JSON includes one."""
    if not isinstance(document, dict):
        return None
    assets = document.get("assets")
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") != name:
            continue
        digest = asset.get("digest")
        if isinstance(digest, str) and digest.startswith("sha256:"):
            value = digest.split(":", 1)[1]
            if len(value) == 64 and all(
                char in "0123456789abcdefABCDEF" for char in value
            ):
                return value.casefold()
    return None


def _require(failure: Failure | None, action: str) -> None:
    if failure is not None:
        raise SystemExit(f"{action}: {failure.message}")


def _seven_zip() -> str:
    for name in _SEVEN_ZIP_NAMES:
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("7-Zip is not installed. Expected 7zz or 7z on PATH.")


def _download(url: str, destination: Path, minimum: int) -> None:
    client = UrllibClient()
    _require(
        download_file(url, destination, client, minimum_bytes=minimum),
        f"download {url}",
    )


def _copy_package(destination: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "src" / "cursor_portable"
    target = destination / "cursor_portable"
    shutil.copytree(source, target, dirs_exist_ok=True)
    write_version_stamp(target, __version__)


def _site_packages(python_root: Path) -> Path:
    matches = list(python_root.glob("Lib/site-packages"))
    matches.extend(python_root.glob("lib/python*/site-packages"))
    if not matches:
        raise SystemExit(f"No site-packages under {python_root}.")
    return matches[0]


def pack(system_name: str, arch: str, output: Path) -> int:
    """Build one self-extractor. A size or hash failure stops the job."""
    host = _HOST.get(system_name)
    if host is None:
        raise SystemExit(f"Unknown system {system_name}.")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cursor-portable-pack-") as temporary:
        work = Path(temporary)
        python_root = _pack_python(system_name, arch, work)
        outer = work / "outer"
        outer.mkdir()
        shutil.copytree(python_root, outer / "python")
        _install_wizard_qt(outer / "python", system_name, arch)
        _copy_package(_site_packages(outer / "python"))
        _bundle_extractors(host, outer / "tools")
        setup = output / setup_asset_name(host, arch)
        _write_setup(host, outer, setup)
    digest = file_sha256(setup)
    sums = output / f"{setup.name}.sha256"
    sums.write_text(render_sha256sums(((digest, setup.name),)), encoding="utf-8")
    print(setup)
    return 0


def _pack_python(system_name: str, arch: str, work: Path) -> Path:
    client = UrllibClient()
    document = client.get(PYTHON_RELEASE, timeout=60, headers=_github_headers())
    if isinstance(document, Failure):
        raise SystemExit(document.message)
    parsed: object = json.loads(document.body.decode("utf-8"))
    marker = ASSET_MARKERS[f"{system_name}-{arch}"]
    name = select_python_asset(asset_names(parsed), marker)
    if name is None:
        raise SystemExit(f"No CPython {PYTHON_MINOR} asset for {marker}.")
    url = asset_url(parsed, name)
    if url is None:
        raise SystemExit(f"Release JSON has no URL for {name}.")
    expected = _python_sha256(parsed, name, work)
    archive = work / name
    _download(url, archive, 5_000_000)
    _require(
        check_download(archive, minimum_bytes=5_000_000, expected_sha256=expected),
        f"hash {name}",
    )
    destination = work / "python-src"
    destination.mkdir()
    with tarfile.open(archive, "r:*") as handle:
        handle.extractall(destination, filter="data")
    roots = [
        path
        for path in destination.rglob("python3")
        if path.parent.name == "bin" and path.is_file()
    ]
    roots.extend(path for path in destination.rglob("python.exe") if path.is_file())
    if not roots:
        raise SystemExit("The Python archive has no interpreter.")
    chosen = roots[0]
    if chosen.name == "python3":
        return chosen.parents[1]
    return chosen.parent


def _python_sha256(document: object, name: str, work: Path) -> str:
    found = asset_sha256(document, name)
    if found is not None:
        return found
    sums_url = asset_url(document, "SHA256SUMS")
    if sums_url is None:
        raise SystemExit(f"No SHA-256 published for {name}.")
    sums_path = work / "SHA256SUMS"
    _download(sums_url, sums_path, 1)
    listed = expected_sha256(sums_path.read_text(encoding="utf-8"), name)
    if listed is None:
        raise SystemExit(f"No SHA-256 published for {name}.")
    return listed


def _github_headers() -> dict[str, str]:
    """Return a GitHub token header when Actions provided one."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def _python_interpreter(python_root: Path) -> Path:
    interpreter = python_root / "python.exe"
    if interpreter.is_file():
        return interpreter
    return python_root / "bin" / "python3"


def cross_qt_command(
    site_packages: Path, system_name: str, arch: str, *, uv: str | None = None
) -> list[str] | Failure | None:
    """Return a host command that installs Qt wheels for another CPU."""
    platform = _CROSS_PLATFORM.get((system_name, arch))
    if platform is None:
        return None
    resolved = uv if uv is not None else shutil.which("uv")
    if not resolved:
        return Failure("command", "uv is not on PATH")
    return [
        resolved,
        "pip",
        "install",
        "--target",
        str(site_packages),
        "--python-platform",
        platform,
        "--python-version",
        PYTHON_MINOR,
        "--only-binary",
        ":all:",
        "--link-mode",
        "copy",
        "--no-config",
        PYSIDE_REQUIREMENT,
    ]


def _install_qt_for_other_cpu(
    python_root: Path,
    system_name: str,
    arch: str,
    failure: Failure,
) -> Failure | None:
    """Install Qt wheels when the packed interpreter cannot run here."""
    cross = cross_qt_command(_site_packages(python_root), system_name, arch)
    if cross is None:
        return failure
    if isinstance(cross, Failure):
        return cross
    LOGGER.warning(
        "Could not run the packed interpreter (%s); installing %s wheels with uv",
        failure.message,
        arch,
    )
    return run_command(cross)


def _install_wizard_qt(python_root: Path, system_name: str, arch: str) -> None:
    interpreter = _python_interpreter(python_root)
    failure = run_command(
        [str(interpreter), "-m", "pip", "install", PYSIDE_REQUIREMENT]
    )
    if failure is None:
        return
    _require(
        _install_qt_for_other_cpu(python_root, system_name, arch, failure),
        "install PySide6 for the wizard",
    )


def _bundle_extractors(host: HostSystem, tools: Path) -> None:
    """Copy 7-Zip into the setup, and innoextract on Windows."""
    tools.mkdir()
    seven = Path(_seven_zip())
    shutil.copy2(seven, tools / seven.name)
    library = seven.parent / "7z.dll"
    if library.is_file():
        shutil.copy2(library, tools / library.name)
    if host is not HostSystem.WINDOWS:
        return
    found = innoextract_executable(tools, allow_path=False)
    if isinstance(found, Failure):
        raise SystemExit(found.message)
    executable = Path(found)
    if executable.parent != tools:
        shutil.copy2(executable, tools / executable.name)
        library_path = executable.parent / "libbz2-1.dll"
        if library_path.is_file():
            shutil.copy2(library_path, tools / library_path.name)
    archive = tools / "innoextract670.zip"
    archive.unlink(missing_ok=True)


def _write_setup(host: HostSystem, outer: Path, setup: Path) -> None:
    """Write the self-extractor. Intermediate archives stay out of the output."""
    seven = _seven_zip()
    with tempfile.TemporaryDirectory(prefix="cursor-portable-sfx-") as temporary:
        work = Path(temporary)
        outer_archive = work / "outer.7z"
        _require(
            run_command(seven_zip_add(seven, outer_archive, outer)), "archive setup"
        )
        archive = outer_archive.read_bytes()
        if host is HostSystem.WINDOWS:
            module = _sfx_module(work)
            setup.write_bytes(build_windows_sfx(module.read_bytes(), archive))
            return
        setup.write_bytes(build_posix_sfx(Path(seven).read_bytes(), archive))


def _sfx_module(directory: Path) -> Path:
    found = os.environ.get("CURSOR_PORTABLE_SFX")
    if found:
        return Path(found)
    extra = directory / "lzma-sdk.7z"
    _download(SFX_SDK, extra, 100_000)
    _require(
        check_download(extra, minimum_bytes=100_000, expected_sha256=SFX_SHA256),
        "hash 7-Zip SDK",
    )
    unpacked = directory / "7z-extra"
    unpacked.mkdir(exist_ok=True)
    _require(
        run_command([_seven_zip(), "x", f"-o{unpacked}", "-y", str(extra)]),
        "extract 7zSD.sfx",
    )
    matches = list(unpacked.rglob("7zSD.sfx"))
    if not matches:
        raise SystemExit("7-Zip SDK did not contain 7zSD.sfx.")
    return matches[0]


def main(argv: list[str] | None = None) -> int:
    """Pack a tool-only setup for one operating system and CPU."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    return pack(args.system, args.arch, Path(args.output))


if __name__ == "__main__":
    raise SystemExit(main())
