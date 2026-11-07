#!/usr/bin/env python3
"""Install portable CPython on a loop volume and check pip and uv.

This process runs inside the Linux image. It downloads the relocatable
interpreter, prepends it for one stub Cursor session, and checks that the
container's pip and uv are unchanged afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

from cursor_portable.cli.launch_cmd import uninstall_host
from cursor_portable.cli.live import install_payload
from cursor_portable.constants import PATH_BLOCK_START
from cursor_portable.disk.volume import Volume
from cursor_portable.failure import Failure
from cursor_portable.install.acquire import copy_tool_package, ensure_python, host_arch
from cursor_portable.models import (
    HostSystem,
    InstallChoices,
    InstallScope,
    ProductKind,
    ProfileMode,
)
from cursor_portable.repack.layout import StickLayout

LOGGER = logging.getLogger("docker_session")
_HOST_PIP = "/usr/local/bin/pip"
_HOST_UV = "/usr/local/bin/uv"
_WHICH_ORDER = ("python", "pip", "uv")
_WHICH_NAMES = frozenset(_WHICH_ORDER)
_RC_NAMES = (".profile", ".bashrc")
_IMAGE = Path(tempfile.gettempdir()) / "cursor-portable-stick.img"
_MOUNT = Path("/mnt/cursor-portable-stick")
_LABEL = "PORTABLE"
_IMAGE_BYTES = 1024 * 1024 * 1024
_READY = Path(tempfile.gettempdir()) / "cursor-session-ready"
_STOP = Path(tempfile.gettempdir()) / "cursor-session-stop"
_READY_TIMEOUT = 90.0
_EXIT_TIMEOUT = 60.0
_STUB = "\n".join(
    (
        "#!/bin/sh",
        "set -eu",
        "printf '%s\\n' ready > \"${CURSOR_SESSION_READY}\"",
        'while [ ! -f "${CURSOR_SESSION_STOP}" ]; do',
        "    sleep 0.2",
        "done",
        "",
    )
)


@dataclass(frozen=True)
class BinaryIdentity:
    """SHA-256 of one host file recorded before the session."""

    path: str
    digest: str


def main(argv: list[str] | None = None) -> int:
    """Run the container session. Return a process status."""
    del argv
    _configure_logging()
    if platform.system() != "Linux":
        LOGGER.error("docker_session.py runs inside the Linux container.")
        return 2
    home = _session_home()
    before_which = _which_map()
    mismatch = _expect_host_tools(before_which)
    if mismatch is not None:
        LOGGER.error("%s", mismatch)
        return 1
    before_files = _identities(_host_binaries())
    volume = _create_volume()
    if isinstance(volume, str):
        LOGGER.error("%s", volume)
        return 1
    try:
        return _exercise(home, volume, before_which, before_files)
    finally:
        _unmount()


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )


def _session_home() -> Path:
    chosen = os.environ.get("HOME", "")
    home = Path(chosen) if chosen else Path(tempfile.gettempdir()) / "session-home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home)
    LOGGER.info("Session home is %s", home)
    return home


def _host_binaries() -> tuple[Path, ...]:
    return (Path(_HOST_PIP), Path(_HOST_UV), Path(sys.executable))


def _exercise(
    home: Path,
    volume: Volume,
    before_which: dict[str, str],
    before_files: dict[str, BinaryIdentity],
) -> int:
    layout = _install_stick(volume)
    if isinstance(layout, str):
        LOGGER.error("%s", layout)
        return 1
    problem = _require_stick_bin(layout.python / "bin")
    if problem is not None:
        LOGGER.error("%s", problem)
        return 1
    _write_stub(layout.app)
    code = _run_launch(home, layout, before_files)
    if code != 0:
        return code
    return _finish(layout, before_which, before_files)


def _finish(
    layout: StickLayout,
    before_which: dict[str, str],
    before_files: dict[str, BinaryIdentity],
) -> int:
    removed = uninstall_host()
    LOGGER.info("Uninstall returned %s", removed)
    if removed != 0:
        return removed
    restored = _expect_same_which(before_which)
    if restored is not None:
        LOGGER.error("%s", restored)
        return 1
    changed = _identities_match(before_files)
    if changed is not None:
        LOGGER.error("%s", changed)
        return 1
    interpreter = layout.python / "bin" / "python3"
    if not interpreter.is_file():
        LOGGER.error("The stick interpreter was removed from %s", interpreter)
        return 1
    LOGGER.info("Host pip and uv match the snapshot. The stick Python remains.")
    return 0


def _install_stick(volume: Volume) -> StickLayout | str:
    payload = Path(
        os.environ.get(
            "CURSOR_PORTABLE_CACHE",
            str(Path(tempfile.gettempdir()) / "cursor-portable-payload"),
        )
    )
    arch = host_arch()
    if isinstance(arch, Failure):
        return arch.message
    LOGGER.info("Downloading portable Python for %s into %s", arch, payload)
    ready = ensure_python(
        HostSystem.LINUX,
        payload,
        arch=arch,
        progress=_progress,
    )
    if ready is not None:
        return ready.message
    copied = copy_tool_package(payload / "tool")
    if copied is not None:
        return copied.message
    installed = install_payload(
        _choices(volume, arch),
        volume,
        payload,
        system=HostSystem.LINUX,
        progress=_progress,
    )
    if installed is not None:
        return installed.message
    return StickLayout.from_mount(_MOUNT)


def _choices(volume: Volume, arch: str) -> InstallChoices:
    return InstallChoices(
        product=ProductKind.CLI,
        scope=InstallScope.USER,
        profile=ProfileMode.FRESH,
        volume_device=volume.device_id,
        typed_label=volume.label,
        target_os=HostSystem.LINUX,
        target_arch=arch,
    )


def _progress(message: str) -> None:
    LOGGER.info("%s", message)


def _require_stick_bin(bin_dir: Path) -> str | None:
    names = sorted(path.name for path in bin_dir.iterdir()) if bin_dir.is_dir() else []
    LOGGER.info("Stick Python bin contains %s", ", ".join(names))
    missing = [name for name in ("python", "pip") if not (bin_dir / name).exists()]
    if missing:
        return f"Stick Python bin is missing {', '.join(missing)}"
    return None


def _write_stub(app: Path) -> None:
    app.mkdir(parents=True, exist_ok=True)
    target = app / "cursor"
    target.write_text(_STUB, encoding="utf-8", newline="\n")
    target.chmod(0o755)
    LOGGER.info("Wrote stub Cursor at %s", target)


def _run_launch(
    home: Path,
    layout: StickLayout,
    before_files: dict[str, BinaryIdentity],
) -> int:
    process = _start_session(layout.root)
    try:
        return _observe_session(home, layout, process, before_files)
    finally:
        _stop_session(process)


def _observe_session(
    home: Path,
    layout: StickLayout,
    process: subprocess.Popen[bytes],
    before_files: dict[str, BinaryIdentity],
) -> int:
    if not _wait_for(_READY, process, _READY_TIMEOUT):
        return 1
    if not _block_state(home, present=True):
        return 1
    problem = _expect_session_paths(layout.python / "bin")
    if problem is not None:
        LOGGER.error("%s", problem)
        return 1
    changed = _identities_match(before_files)
    if changed is not None:
        LOGGER.error("%s", changed)
        return 1
    _STOP.write_text("stop\n", encoding="utf-8")
    code = _wait_exit(process)
    if code != 0:
        return code
    if not _block_state(home, present=False):
        return 1
    return 0


def _start_session(root: Path) -> subprocess.Popen[bytes]:
    _READY.unlink(missing_ok=True)
    _STOP.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment["CURSOR_SESSION_READY"] = str(_READY)
    environment["CURSOR_SESSION_STOP"] = str(_STOP)
    LOGGER.info("Launching %s", root)
    return subprocess.Popen(
        [sys.executable, "-m", "cursor_portable", "launch", "--root", str(root)],
        env=environment,
    )


def _stop_session(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    _STOP.write_text("stop\n", encoding="utf-8")
    try:
        code = process.wait(timeout=_EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        LOGGER.error("Stopping the launch process")
        process.kill()
        process.wait(timeout=15)
        return
    LOGGER.info("Launch process ended with %s", code)


def _wait_for(path: Path, process: subprocess.Popen[bytes], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            LOGGER.info("Saw %s", path)
            return True
        if process.poll() is not None:
            LOGGER.error(
                "Launch exited %s before the stub was ready",
                process.returncode,
            )
            return False
        time.sleep(0.2)
    LOGGER.error("Timed out waiting for %s", path)
    return False


def _wait_exit(process: subprocess.Popen[bytes]) -> int:
    try:
        code = process.wait(timeout=_EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        LOGGER.error("Launch did not exit")
        return 1
    LOGGER.info("Launch exited %s", code)
    return code


def _block_state(home: Path, *, present: bool) -> bool:
    for name in _RC_NAMES:
        text = _rc_text(home, name)
        found = PATH_BLOCK_START in text
        if found == present:
            continue
        LOGGER.error("Portable PATH block in %s present=%s", name, found)
        LOGGER.error("%s", text)
        return False
    return True


def _rc_text(home: Path, name: str) -> str:
    path = home / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _expect_host_tools(found: dict[str, str]) -> str | None:
    pip = found.get("pip", "")
    uv = found.get("uv", "")
    if pip != _HOST_PIP:
        return f"pip resolved to {pip or '(absent)'}"
    if uv != _HOST_UV:
        return f"uv resolved to {uv or '(absent)'}"
    return None


def _expect_session_paths(bin_dir: Path) -> str | None:
    python = _login_which("python")
    pip = _login_which("pip")
    uv = _login_which("uv")
    if not _on_stick(python, bin_dir):
        return f"python resolved to {python or '(absent)'}"
    if not _on_stick(pip, bin_dir):
        return f"pip resolved to {pip or '(absent)'}"
    if uv != _HOST_UV:
        return f"uv resolved to {uv or '(absent)'}"
    return None


def _expect_same_which(before: dict[str, str]) -> str | None:
    for name in _WHICH_ORDER:
        current = _login_which(name)
        if current != before[name]:
            shown = current or "(absent)"
            return f"{name} resolved to {shown} after the session"
    return None


def _on_stick(found: str, bin_dir: Path) -> bool:
    if not found:
        return False
    try:
        Path(found).resolve().relative_to(bin_dir.resolve())
    except ValueError:
        return False
    return True


def _which_map() -> dict[str, str]:
    return {name: _login_which(name) for name in _WHICH_ORDER}


def _login_which(name: str) -> str:
    if name not in _WHICH_NAMES:
        raise ValueError(name)
    completed = _run(["bash", "-lc", f"command -v {name} || true"])
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    found = lines[-1] if lines else ""
    LOGGER.info("Login shell %s -> %s", name, found or "(absent)")
    if completed.returncode != 0:
        LOGGER.error("Login shell stderr: %s", completed.stderr.strip())
    return found


def _identities(paths: tuple[Path, ...]) -> dict[str, BinaryIdentity]:
    found: dict[str, BinaryIdentity] = {}
    for path in paths:
        resolved = path.resolve()
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        LOGGER.info("Recorded %s as %s (%s)", path, digest, resolved)
        found[str(path)] = BinaryIdentity(str(resolved), digest)
    return found


def _identities_match(before: dict[str, BinaryIdentity]) -> str | None:
    for path, identity in before.items():
        current = Path(path).resolve()
        digest = hashlib.sha256(current.read_bytes()).hexdigest()
        if digest == identity.digest and str(current) == identity.path:
            continue
        return (
            f"{path} changed from {identity.path} {identity.digest} "
            f"to {current} {digest}"
        )
    return None


def _create_volume() -> Volume | str:
    _unmount()
    _IMAGE.unlink(missing_ok=True)
    LOGGER.info("Creating ext4 image %s", _IMAGE)
    _IMAGE.touch()
    os.truncate(_IMAGE, _IMAGE_BYTES)
    formatted = _run(["mkfs.ext4", "-F", "-L", _LABEL, str(_IMAGE)])
    if formatted.returncode != 0:
        return (
            formatted.stderr.strip() or formatted.stdout.strip() or "mkfs.ext4 failed"
        )
    _MOUNT.mkdir(parents=True, exist_ok=True)
    mounted = _run(["mount", "-o", "loop", str(_IMAGE), str(_MOUNT)])
    if mounted.returncode != 0:
        return mounted.stderr.strip() or mounted.stdout.strip() or "mount failed"
    return _volume(_mounted_source())


def _mounted_source() -> str:
    completed = _run(["findmnt", "-n", "-o", "SOURCE", str(_MOUNT)])
    source = completed.stdout.strip()
    if completed.returncode != 0 or not source:
        LOGGER.warning("findmnt did not name %s", _MOUNT)
        return "/dev/loop0"
    LOGGER.info("Loop source for %s is %s", _MOUNT, source)
    return source


def _volume(device: str) -> Volume:
    return Volume(
        device_id=device,
        mount_point=str(_MOUNT),
        label=_LABEL,
        filesystem="ext4",
        size_bytes=_IMAGE.stat().st_size,
        removable=True,
        is_system=False,
        is_boot=False,
        writable=True,
    )


def _is_mounted(path: Path) -> bool:
    try:
        text = Path("/proc/mounts").read_text(encoding="utf-8")
    except OSError:
        LOGGER.info("Mount table is not available")
        return False
    target = str(path)
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == target:
            return True
    return False


def _unmount() -> None:
    if not _is_mounted(_MOUNT):
        return
    completed = _run(["umount", str(_MOUNT)])
    if completed.returncode != 0:
        LOGGER.error("umount failed: %s", completed.stderr.strip())


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    LOGGER.info("Running %s", " ".join(command))
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    sys.exit(main())
