"""Download official Cursor files when the wizard was not given a payload."""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import sys
import tarfile
import zipfile

import cursor_portable
from cursor_portable._version import __version__, write_version_stamp
from cursor_portable.constants import MIN_CURSOR_BYTES
from cursor_portable.download.catalog import cli_install_url, ide_target
from cursor_portable.download.cli_script import cli_archive_url
from cursor_portable.download.fetch import download_https
from cursor_portable.download.http_client import UrllibClient
from cursor_portable.download.resolve import resolve_ide
from cursor_portable.download.sum_file import expected_sha256
from cursor_portable.download.verify_file import check_download
from cursor_portable.failure import Failure
from cursor_portable.models import (
    HostSystem,
    InstallChoices,
    InstallScope,
    ProductKind,
    host_system_name,
)
from cursor_portable.python_runtime.pin import (
    ASSET_MARKERS,
    PYTHON_MINOR,
    python_tree_matches,
    select_python_asset,
)
from cursor_portable.repack.archive import run_command
from cursor_portable.repack.commands import innoextract_command
from cursor_portable.repack.extract_ide import application_directory, extract_ide
from cursor_portable.repack.product_json import find_product_json

LOGGER = logging.getLogger(__name__)

Progress = Callable[[str], None]

# Official innoextract 1.9 cannot read the Inno Setup releases Cursor ships.
_INNOEXTRACT_URL = (
    "https://github.com/UserUnknownFactor/innoextract_win/releases/download/"
    "670/innoextract670.zip"
)
_INNOEXTRACT_SHA256 = "79b69b9b1fcd98f42ccd4b245efdf6a03bcfb674ba6af482f5a46891c9ed4d14"
_PYTHON_RELEASE = (
    "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
)
_INNO_FILES = frozenset({"innoextract.exe", "libbz2-1.dll"})


def payload_cache() -> Path:
    """Directory that holds downloaded portable files between wizard runs."""
    override = os.environ.get("CURSOR_PORTABLE_CACHE")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "cursor-portable" / "payload"
    return Path.home() / ".cache" / "cursor-portable" / "payload"


def host_arch() -> str | Failure:
    """Return ``x64`` or ``arm64`` for this computer."""
    machine = platform.machine().casefold()
    if machine in {"amd64", "x86_64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return Failure("arch", f"Unsupported CPU architecture: {machine}.")


def ide_platform_id(
    system: HostSystem,
    arch: str,
    scope: InstallScope,
) -> str | Failure:
    """Return the official Cursor platform id for this computer and build."""
    if system is HostSystem.WINDOWS:
        platform_id = f"win32-{arch}-{scope.value}"
    elif system is HostSystem.MACOS:
        platform_id = f"darwin-{arch}"
    else:
        platform_id = f"linux-{arch}"
    known = ide_target(platform_id)
    if isinstance(known, Failure):
        return known
    return platform_id


def acquire_payload(
    choices: InstallChoices,
    system: HostSystem,
    destination: Path,
    *,
    progress: Progress | None = None,
) -> Failure | None:
    """Fill ``destination`` with the IDE, CLI, Python, and this package."""
    destination.mkdir(parents=True, exist_ok=True)
    LOGGER.info(
        "Acquiring %s %s on %s",
        choices.target_os.value,
        choices.target_arch,
        system.value,
    )
    _report(progress, "Copying the portable launcher")
    copied = copy_tool_package(destination / "tool")
    if copied is not None:
        return copied
    python_ready = ensure_python(
        choices.target_os,
        destination,
        arch=choices.target_arch,
        progress=progress,
    )
    if python_ready is not None:
        return python_ready
    if choices.product in {ProductKind.IDE, ProductKind.BOTH}:
        ide_ready = ensure_ide(choices, system, destination, progress=progress)
        if ide_ready is not None:
            return ide_ready
    if choices.product in {ProductKind.CLI, ProductKind.BOTH}:
        return ensure_cli(
            choices.target_os,
            destination,
            arch=choices.target_arch,
            progress=progress,
        )
    return None


def copy_tool_package(tool_root: Path) -> Failure | None:
    """Copy the running package so the drive launcher can import it."""
    source = Path(cursor_portable.__file__).resolve().parent
    target = tool_root / "cursor_portable"
    tool_root.mkdir(parents=True, exist_ok=True)
    try:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        write_version_stamp(target, __version__)
    except OSError as exc:
        return Failure("tool", f"Could not copy the launcher package: {exc}")
    LOGGER.info("Copied %s to %s", source, target)
    return None


def ensure_python(
    system: HostSystem,
    destination: Path,
    *,
    arch: str | None = None,
    progress: Progress | None = None,
) -> Failure | None:
    """Download the pinned relocatable CPython into ``destination/python``."""
    folder = destination / "python"
    if python_tree_matches(folder):
        LOGGER.info("Using the Python already in %s", folder)
        _report(progress, "Using the portable Python already downloaded")
        return None
    if _python_present(folder):
        LOGGER.info(
            "Replacing Python in %s because it is not CPython %s",
            folder,
            PYTHON_MINOR,
        )
        _report(progress, f"Replacing portable Python with CPython {PYTHON_MINOR}")
    chosen = arch
    if not chosen:
        detected = host_arch()
        if isinstance(detected, Failure):
            return detected
        chosen = detected
    _report(progress, "Downloading portable Python")
    archive = _python_archive(
        system,
        chosen,
        destination / "downloads",
        progress=progress,
    )
    if isinstance(archive, Failure):
        return archive
    extracted = destination / "downloads" / "python-src"
    if extracted.exists():
        shutil.rmtree(extracted)
    extracted.mkdir(parents=True)
    _report(progress, "Extracting portable Python")
    try:
        with tarfile.open(archive, "r:*") as handle:
            handle.extractall(extracted, filter="data")
    except (OSError, tarfile.TarError, TypeError, ValueError) as exc:
        return Failure("python", f"Could not extract {archive.name}: {exc}")
    root = _python_root(extracted)
    if isinstance(root, Failure):
        return root
    if folder.exists():
        shutil.rmtree(folder)
    shutil.copytree(root, folder)
    LOGGER.info("Installed portable Python in %s", folder)
    return None


def ensure_ide(
    choices: InstallChoices,
    system: HostSystem,
    destination: Path,
    *,
    progress: Progress | None = None,
) -> Failure | None:
    """Download and extract the official IDE for ``choices``."""
    arch = choices.target_arch
    if not arch:
        detected = host_arch()
        if isinstance(detected, Failure):
            return detected
        arch = detected
    target = choices.target_os
    platform_id = ide_platform_id(target, arch, choices.scope)
    if isinstance(platform_id, Failure):
        return platform_id
    folder = destination / _ide_folder(target, choices.scope)
    if find_product_json(folder) is not None:
        LOGGER.info("Using the Cursor files already in %s", folder)
        _report(progress, "Using the Cursor IDE already downloaded")
        return None
    client = UrllibClient()
    resolved = resolve_ide(platform_id, client)
    if isinstance(resolved, Failure):
        return resolved
    _report(progress, f"Downloading {resolved.file_name}")
    installer = destination / "downloads" / resolved.file_name
    failure = download_https(
        resolved.url,
        installer,
        minimum_bytes=MIN_CURSOR_BYTES,
        progress=progress,
        progress_label="the Cursor IDE",
    )
    if failure is not None:
        return failure
    extracted = _extract_directory(destination)
    if extracted.exists():
        shutil.rmtree(extracted)
    _report(progress, "Extracting Cursor")
    failure = _extract_installer(
        target,
        installer,
        extracted,
        destination / "tools",
        host=system,
    )
    if failure is not None:
        return failure
    root = application_directory(extracted)
    if isinstance(root, Failure):
        return root
    if folder.exists():
        shutil.rmtree(folder)
    shutil.copytree(root, folder)
    LOGGER.info("Extracted Cursor into %s", folder)
    return None


def ensure_cli(
    system: HostSystem,
    destination: Path,
    *,
    arch: str | None = None,
    progress: Progress | None = None,
) -> Failure | None:
    """Download the official CLI archive into ``destination/cli``."""
    folder = destination / "cli"
    if any(folder.glob("cursor*")):
        LOGGER.info("Using the CLI already in %s", folder)
        _report(progress, "Using the Cursor CLI already downloaded")
        return None
    chosen = _cli_arch(arch)
    if isinstance(chosen, Failure):
        return chosen
    client = UrllibClient()
    script = client.get(
        cli_install_url(windows=system is HostSystem.WINDOWS), timeout=60
    )
    if isinstance(script, Failure):
        return script
    selected = cli_archive_url(
        script.body.decode("utf-8", errors="replace"),
        system,
        chosen,
    )
    if isinstance(selected, Failure):
        return selected
    LOGGER.info("Selected Cursor CLI archive %s", selected)
    _report(progress, "Downloading the Cursor CLI")
    archive = destination / "downloads" / "cli-archive"
    failure = download_https(
        selected,
        archive,
        minimum_bytes=1_000_000,
        progress=progress,
        progress_label="the Cursor CLI",
    )
    if failure is not None:
        return failure
    _report(progress, "Extracting the Cursor CLI")
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    return _extract_cli_archive(archive, folder)


def _cli_arch(arch: str | None) -> str | Failure:
    if arch:
        return arch
    return host_arch()


def _report(progress: Progress | None, message: str) -> None:
    LOGGER.info("%s", message)
    if progress is not None:
        progress(message)


def _extract_directory(destination: Path) -> Path:
    """Return a short directory. innoextract is not long-path aware."""
    return destination.parent / "e"


def _ide_folder(system: HostSystem, scope: InstallScope) -> str:
    if system is HostSystem.WINDOWS:
        return f"ide-{scope.value}"
    return "ide"


def _python_present(folder: Path) -> bool:
    return (folder / "python.exe").is_file() or (folder / "bin" / "python3").is_file()


def _python_root(extracted: Path) -> Path | Failure:
    executables = [path for path in extracted.rglob("python.exe") if path.is_file()]
    if executables:
        return executables[0].parent
    binaries = [
        path
        for path in extracted.rglob("python3")
        if path.is_file() and path.parent.name == "bin"
    ]
    if not binaries:
        return Failure("python", "The Python archive has no interpreter.")
    return binaries[0].parents[1]


def _extract_installer(
    system: HostSystem,
    installer: Path,
    destination: Path,
    tools: Path,
    *,
    host: HostSystem | None = None,
) -> Failure | None:
    destination.mkdir(parents=True, exist_ok=True)
    current = host_system_name() if host is None else host
    if system is HostSystem.WINDOWS:
        return _extract_windows_installer(installer, destination, tools, current)
    seven = _seven_zip()
    if isinstance(seven, Failure):
        return seven
    return extract_ide(system, installer, destination, seven_zip=seven)


def _extract_windows_installer(
    installer: Path,
    destination: Path,
    tools: Path,
    host: HostSystem,
) -> Failure | None:
    """Extract a Windows installer. Linux uses a native innoextract."""
    if host is not HostSystem.WINDOWS:
        found = shutil.which("innoextract")
        if not found:
            return Failure(
                "innoextract",
                "A Windows stick made on this computer needs innoextract on PATH. "
                "The Windows innoextract program is not run here.",
            )
        command = innoextract_command(installer, destination)
        command[0] = found
        return run_command(command)
    extractor = innoextract_executable(tools)
    if isinstance(extractor, Failure):
        return extractor
    command = innoextract_command(installer, destination)
    command[0] = extractor
    return run_command(command)


def bundled_tool(names: tuple[str, ...]) -> Path | None:
    """Return an extractor shipped beside this interpreter."""
    executable = Path(sys.executable).resolve()
    roots = (
        executable.parent,
        executable.parent.parent,
        executable.parent.parent.parent,
    )
    for root in roots:
        tools = root / "tools"
        for name in names:
            candidate = tools / name
            if candidate.is_file():
                return candidate
    return None


def _seven_zip() -> str | Failure:
    bundled = bundled_tool(("7zz", "7z", "7zz.exe", "7z.exe"))
    if bundled is not None:
        return str(bundled)
    for name in ("7zz", "7z", "7zz.exe", "7z.exe"):
        found = shutil.which(name)
        if found:
            return found
    return Failure("7zip", "7-Zip is required to extract this Cursor build.")


def innoextract_executable(tools: Path, *, allow_path: bool = True) -> str | Failure:
    """Return innoextract, downloading a pinned Windows build when it is absent."""
    bundled = bundled_tool(("innoextract.exe",))
    if bundled is not None and (bundled.parent / "libbz2-1.dll").is_file():
        return str(bundled)
    if allow_path:
        found = shutil.which("innoextract")
        if found:
            LOGGER.info("Using innoextract from PATH: %s", found)
            return found
    LOGGER.info("Using the pinned innoextract build")
    tools.mkdir(parents=True, exist_ok=True)
    executable = tools / "innoextract.exe"
    library = tools / "libbz2-1.dll"
    if executable.is_file() and library.is_file():
        return str(executable)
    archive = tools / "innoextract670.zip"
    failure = download_https(
        _INNOEXTRACT_URL,
        archive,
        minimum_bytes=100_000,
        expected_sha256=_INNOEXTRACT_SHA256,
    )
    if failure is not None:
        return failure
    extracted = _extract_innoextract_zip(archive, tools)
    if extracted is not None:
        return extracted
    if not executable.is_file():
        return Failure("innoextract", "The innoextract archive has no innoextract.exe.")
    return str(executable)


def _extract_innoextract_zip(archive: Path, tools: Path) -> Failure | None:
    try:
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                name = Path(info.filename).name
                if name not in _INNO_FILES or info.is_dir():
                    continue
                target = tools / name
                with handle.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        return Failure("innoextract", f"Could not unpack innoextract: {exc}")
    return None


def _extract_cli_archive(archive: Path, folder: Path) -> Failure | None:
    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as handle:
                for info in handle.infolist():
                    parts = Path(info.filename).parts
                    if info.filename.startswith(("/", "\\")) or ".." in parts:
                        return Failure(
                            "cli", f"Refusing archive member {info.filename}."
                        )
                    handle.extract(info, folder)
            return None
        with tarfile.open(archive, "r:*") as handle:
            handle.extractall(folder, filter="data")
    except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError) as exc:
        return Failure("cli", f"Could not extract the CLI archive: {exc}")
    return None


def _python_archive(
    system: HostSystem,
    arch: str,
    downloads: Path,
    *,
    progress: Progress | None = None,
) -> Path | Failure:
    client = UrllibClient()
    parsed = _python_release(client)
    if isinstance(parsed, Failure):
        return parsed
    selected = _python_choice(parsed, system, arch)
    if isinstance(selected, Failure):
        return selected
    name, url = selected
    expected = _python_sha256(parsed, name, downloads)
    if isinstance(expected, Failure):
        return expected
    archive = downloads / name
    failure = download_https(
        url,
        archive,
        minimum_bytes=5_000_000,
        expected_sha256=expected,
        progress=progress,
        progress_label="portable Python",
    )
    if failure is not None:
        return failure
    confirmed = check_download(
        archive, minimum_bytes=5_000_000, expected_sha256=expected
    )
    if confirmed is not None:
        return confirmed
    return archive


def _python_release(client: UrllibClient) -> dict[str, object] | Failure:
    response = client.get(_PYTHON_RELEASE, timeout=60)
    if isinstance(response, Failure):
        return response
    try:
        parsed: object = json.loads(response.body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        return Failure("python", f"Python release JSON is invalid: {exc}")
    if not isinstance(parsed, dict):
        return Failure("python", "Python release JSON is not an object.")
    return parsed


def _python_choice(
    document: dict[str, object],
    system: HostSystem,
    arch: str,
) -> tuple[str, str] | Failure:
    marker = ASSET_MARKERS.get(f"{system.value}-{arch}")
    if marker is None:
        return Failure("python", f"No portable Python build for {system.value} {arch}.")
    name = select_python_asset(_asset_names(document), marker)
    if name is None:
        return Failure("python", f"No CPython {PYTHON_MINOR} asset for {marker}.")
    LOGGER.info("Selected portable CPython asset %s", name)
    url = _asset_url(document, name)
    if url is None:
        return Failure("python", f"Release JSON has no URL for {name}.")
    return name, url


def _asset_names(document: dict[str, object]) -> tuple[str, ...]:
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


def _asset_url(document: dict[str, object], name: str) -> str | None:
    assets = document.get("assets")
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") == name:
            url = asset.get("browser_download_url")
            if isinstance(url, str):
                return url
    return None


def _asset_sha256(document: dict[str, object], name: str) -> str | None:
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


def _python_sha256(
    document: dict[str, object],
    name: str,
    downloads: Path,
) -> str | Failure:
    found = _asset_sha256(document, name)
    if found is not None:
        return found
    sums_url = _asset_url(document, "SHA256SUMS")
    if sums_url is None:
        return Failure("python", f"No SHA-256 published for {name}.")
    sums_path = downloads / "SHA256SUMS"
    failure = download_https(sums_url, sums_path, minimum_bytes=1)
    if failure is not None:
        return failure
    listed = expected_sha256(sums_path.read_text(encoding="utf-8"), name)
    if listed is None:
        return Failure("python", f"No SHA-256 published for {name}.")
    return listed
