"""Extract an official Cursor IDE installer without running it."""

from __future__ import annotations

from enum import Enum
import logging
from pathlib import Path
import shutil
import stat
import tarfile
import zipfile

from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem
from cursor_portable.repack.archive import run_command
from cursor_portable.repack.commands import (
    appimage_extract_command,
    dmg_attach_command,
    dmg_detach_command,
    innoextract_command,
)
from cursor_portable.repack.product_json import application_root, find_product_json

LOGGER = logging.getLogger(__name__)


class InstallerKind(Enum):
    """How an official IDE download is packed."""

    INNO = "inno"
    DMG = "dmg"
    APPIMAGE = "appimage"
    TAR = "tar"
    DEB = "deb"
    ZIP = "zip"


def installer_kind(path: Path) -> InstallerKind | Failure:
    """Classify an official download by its file name."""
    name = path.name.casefold()
    if name.endswith(".exe"):
        return InstallerKind.INNO
    if name.endswith(".dmg"):
        return InstallerKind.DMG
    if name.endswith(".appimage"):
        return InstallerKind.APPIMAGE
    if name.endswith((".tar.gz", ".tgz", ".tar.xz", ".tar")):
        return InstallerKind.TAR
    if name.endswith(".deb"):
        return InstallerKind.DEB
    if name.endswith(".zip"):
        return InstallerKind.ZIP
    return Failure("installer-kind", f"Unrecognized Cursor download: {path.name}")


def application_directory(extracted: Path) -> Path | Failure:
    """Return the directory that should be copied as the portable app."""
    product = find_product_json(extracted)
    if product is None:
        return Failure("product-json", f"No product.json under {extracted}.")
    root = application_root(product)
    if root is None:
        return Failure("product-json", f"Unexpected product.json location: {product}.")
    return root


def extract_ide(
    system: HostSystem,
    installer: Path,
    destination: Path,
    *,
    seven_zip: str,
    innoextract: str = "innoextract",
) -> Failure | None:
    """Extract an IDE installer into ``destination`` without executing it."""
    kind = installer_kind(installer)
    if isinstance(kind, Failure):
        return kind
    destination.mkdir(parents=True, exist_ok=True)
    if kind is InstallerKind.INNO:
        command = innoextract_command(installer, destination)
        command[0] = innoextract
        LOGGER.info("Extracting %s with %s", installer.name, innoextract)
        return run_command(command)
    if kind is InstallerKind.DMG:
        return _extract_dmg(installer, destination)
    if kind is InstallerKind.APPIMAGE:
        return _extract_appimage(installer, destination, seven_zip)
    if kind is InstallerKind.TAR:
        return _extract_tar(installer, destination)
    if kind is InstallerKind.DEB:
        return _extract_deb(system, installer, destination)
    return _extract_zip(installer, destination)


def _extract_dmg(installer: Path, destination: Path) -> Failure | None:
    mount = destination / "mnt"
    mount.mkdir(parents=True, exist_ok=True)
    attached = run_command(dmg_attach_command(installer, mount))
    if attached is not None:
        return attached
    try:
        failure = _copy_app(mount, destination, installer.name)
    finally:
        detached = run_command(dmg_detach_command(mount))
        if detached is not None:
            LOGGER.error("%s", detached.message)
    return failure


def _copy_app(mount: Path, destination: Path, label: str) -> Failure | None:
    apps = sorted(mount.glob("*.app"))
    if not apps:
        apps = sorted(path for path in mount.rglob("*.app") if path.is_dir())
    if not apps:
        return Failure("dmg", f"{label} does not contain an application.")
    try:
        shutil.copytree(
            apps[0],
            destination / apps[0].name,
            symlinks=True,
            dirs_exist_ok=True,
        )
    except OSError as exc:
        return Failure("dmg", f"Could not copy {apps[0].name}: {exc}")
    return None


def _extract_appimage(
    installer: Path,
    destination: Path,
    seven_zip: str,
) -> Failure | None:
    extracted = run_command([seven_zip, "x", f"-o{destination}", "-y", str(installer)])
    if extracted is None and find_product_json(destination) is not None:
        LOGGER.info("Extracted %s with 7-Zip", installer.name)
        return None
    mode = installer.stat().st_mode
    installer.chmod(mode | stat.S_IEXEC)
    fallback = run_command(
        appimage_extract_command(installer),
        cwd=destination,
    )
    if fallback is not None:
        return fallback
    if find_product_json(destination) is None:
        return Failure("appimage", f"Could not extract {installer.name}.")
    return None


def _extract_tar(installer: Path, destination: Path) -> Failure | None:
    try:
        with tarfile.open(installer, "r:*") as handle:
            handle.extractall(destination, filter="data")
    except (OSError, tarfile.TarError, ValueError) as exc:
        return Failure("tar", f"Could not extract {installer.name}: {exc}")
    return None


def _extract_deb(
    system: HostSystem,
    installer: Path,
    destination: Path,
) -> Failure | None:
    if system is not HostSystem.LINUX:
        return Failure("deb", "Debian packages are only extracted on Linux.")
    return run_command(["dpkg-deb", "-x", str(installer), str(destination)])


def _extract_zip(installer: Path, destination: Path) -> Failure | None:
    try:
        with zipfile.ZipFile(installer) as handle:
            for info in handle.infolist():
                parts = Path(info.filename).parts
                if info.filename.startswith(("/", "\\")) or ".." in parts:
                    return Failure("zip", f"Refusing archive member {info.filename}.")
                handle.extract(info, destination)
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        return Failure("zip", f"Could not extract {installer.name}: {exc}")
    return None
