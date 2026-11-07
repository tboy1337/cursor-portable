"""Copy a prepared app tree onto the removable drive."""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
import shutil
import stat

from cursor_portable.disk.filesystem import display_filesystem, filesystem_for
from cursor_portable.failure import Failure
from cursor_portable.git_runtime.pin import git_executable, git_tree_matches
from cursor_portable.json_io import load_object
from cursor_portable.launcher.launch_plan import interpreter_path
from cursor_portable.models import (
    HostSystem,
    InstallChoices,
    InstallScope,
    ProductKind,
    ProfileMode,
)
from cursor_portable.profile.copy_profile import copy_profile
from cursor_portable.profile.settings import set_git_path, set_interpreter
from cursor_portable.profile.sources import ProfileSources
from cursor_portable.python_runtime.pin import PYTHON_MINOR, python_tree_matches
from cursor_portable.repack.copy_tree import copy_tree
from cursor_portable.repack.launchers import unix_launcher_text, windows_launcher_text
from cursor_portable.repack.layout import StickLayout, ensure_data_dirs
from cursor_portable.repack.meta import write_version_file
from cursor_portable.repack.product_json import (
    find_product_json,
    read_commit,
    read_product_version,
    strip_product_file,
)

LOGGER = logging.getLogger(__name__)


def _version_from_app(app: Path) -> tuple[str, str]:
    product = find_product_json(app)
    if product is None:
        return "unknown", ""
    failure = strip_product_file(product)
    if failure is not None:
        LOGGER.error("%s", failure.message)
        return "unknown", ""
    loaded = load_object(product.read_text(encoding="utf-8"))
    if isinstance(loaded, Failure):
        return "unknown", ""
    return read_product_version(loaded), read_commit(loaded)


def _report_install(
    progress: Callable[[str], None] | None,
    message: str,
) -> None:
    LOGGER.info("%s", message)
    if progress is not None:
        progress(message)


def _copy_named(
    source: Path,
    destination: Path,
    label: str,
    root: Path,
    progress: Callable[[str], None] | None,
) -> int:
    _report_install(
        progress,
        f"Copying {label} onto {root}. This can take several minutes.",
    )
    count = copy_tree(source, destination)
    _report_install(progress, f"Copied {count} {label} files onto {root}.")
    return count


def _copy_products(
    source_app: Path,
    layout: StickLayout,
    choices: InstallChoices,
    python_source: Path | None,
    cli_source: Path | None,
    tool_source: Path | None,
    git_source: Path | None,
    progress: Callable[[str], None] | None,
) -> tuple[str, str] | Failure:
    version = "unknown"
    commit = ""
    if choices.product in {ProductKind.IDE, ProductKind.BOTH}:
        if not source_app.is_dir():
            return Failure("missing-app", f"IDE files were not found at {source_app}.")
        _copy_named(source_app, layout.app, "Cursor IDE", layout.root, progress)
        version, commit = _version_from_app(layout.app)
    if (
        choices.product in {ProductKind.CLI, ProductKind.BOTH}
        and cli_source is not None
        and cli_source.is_dir()
    ):
        _copy_named(cli_source, layout.cli, "Cursor CLI", layout.root, progress)
    if python_source is not None and python_source.is_dir():
        prepared = _prepare_python_destination(layout.python)
        if prepared is not None:
            return prepared
        _copy_named(
            python_source,
            layout.python,
            "portable Python",
            layout.root,
            progress,
        )
    if git_source is not None and git_source.is_dir():
        prepared_git = _prepare_git_destination(layout.git)
        if prepared_git is not None:
            return prepared_git
        _copy_named(
            git_source,
            layout.git,
            "portable Git",
            layout.root,
            progress,
        )
    if tool_source is not None and tool_source.is_dir():
        _copy_named(tool_source, layout.tool, "the launcher", layout.root, progress)
    return version, commit


def _prepare_python_destination(destination: Path) -> Failure | None:
    """Remove a cached interpreter that is not the pinned CPython minor."""
    if not destination.exists() or python_tree_matches(destination):
        return None
    LOGGER.info(
        "Removing %s because it is not CPython %s",
        destination,
        PYTHON_MINOR,
    )
    try:
        shutil.rmtree(destination)
    except OSError as exc:
        return Failure("python", f"Could not replace Python in {destination}: {exc}")
    return None


def _prepare_git_destination(destination: Path) -> Failure | None:
    """Remove a Git directory that does not contain a portable executable."""
    if not destination.exists() or git_tree_matches(destination):
        return None
    LOGGER.info("Removing %s because it has no git executable", destination)
    try:
        shutil.rmtree(destination)
    except OSError as exc:
        return Failure("git", f"Could not replace Git in {destination}: {exc}")
    return None


def _finish_profile(
    layout: StickLayout,
    choices: InstallChoices,
    sources: ProfileSources | None,
    cursor_running: bool,
    progress: Callable[[str], None] | None,
) -> None:
    if choices.profile is ProfileMode.FRESH:
        _report_install(progress, "Creating an empty profile")
        _clear_profile(layout)
        LOGGER.info("Removed the portable profile on %s", layout.root)
        return
    if sources is None:
        _report_install(progress, "Leaving the portable profile empty")
        LOGGER.info("Leaving the portable profile empty")
        return
    _report_install(progress, "Copying settings from this computer")
    copy_profile(
        sources,
        layout,
        cursor_running=cursor_running,
        include_state_backup=choices.include_state_backup,
    )
    if choices.product in {ProductKind.IDE, ProductKind.BOTH}:
        settings = layout.user_data / "User" / "settings.json"
        set_interpreter(settings, interpreter_path(layout, choices.target_os))
        _set_git_setting(settings, layout.git, choices.target_os)


def _set_git_setting(settings: Path, folder: Path, system: HostSystem) -> None:
    executable = git_executable(folder, system)
    if executable.is_file():
        set_git_path(settings, executable)


def install_tree(  # pylint: disable=too-many-arguments
    source_app: Path,
    layout: StickLayout,
    choices: InstallChoices,
    system: HostSystem,
    *,
    sources: ProfileSources | None,
    cursor_running: bool,
    python_source: Path | None = None,
    cli_source: Path | None = None,
    tool_source: Path | None = None,
    git_source: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> Failure | None:
    """Install the selected product onto the stick layout."""
    if choices.profile is ProfileMode.COPY and choices.target_os is not system:
        return Failure(
            "profile",
            "Settings from this computer are only copied onto a stick for the same system.",
        )
    LOGGER.info(
        "Installing %s %s into %s",
        choices.target_os.value,
        choices.target_arch,
        layout.root,
    )
    ensure_data_dirs(layout)
    copied = _copy_products(
        source_app,
        layout,
        choices,
        python_source,
        cli_source,
        tool_source,
        git_source,
        progress,
    )
    if isinstance(copied, Failure):
        return copied
    version, commit = copied
    _report_install(progress, "Writing the launcher")
    _write_launcher(layout, choices.target_os)
    write_version_file(
        layout.version_file,
        cursor_version=version,
        commit=commit,
        scope=choices.scope,
        product=choices.product,
        target_os=choices.target_os.value,
        target_arch=choices.target_arch,
        filesystem=display_filesystem(filesystem_for(choices.target_os)),
    )
    _finish_profile(layout, choices, sources, cursor_running, progress)
    return None


def _write_launcher(layout: StickLayout, target: HostSystem) -> None:
    if target is HostSystem.WINDOWS:
        layout.windows_launcher.write_bytes(windows_launcher_text().encode("utf-8"))
        if layout.unix_launcher.exists():
            layout.unix_launcher.unlink()
        return
    layout.unix_launcher.write_text(unix_launcher_text(), encoding="utf-8")
    current = layout.unix_launcher.stat().st_mode
    layout.unix_launcher.chmod(current | stat.S_IEXEC)
    if layout.windows_launcher.exists():
        layout.windows_launcher.unlink()


def _clear_profile(layout: StickLayout) -> None:
    for path in (layout.user_data, layout.extensions, layout.dot_cursor):
        if path.exists():
            LOGGER.info("Removing %s", path)
            shutil.rmtree(path)
    ensure_data_dirs(layout)


def scope_dir(payload: Path, scope: InstallScope) -> Path:
    """Return the IDE directory for a Windows scope, or the only IDE directory."""
    named = payload / f"ide-{scope.value}"
    if named.is_dir():
        return named
    return payload / "ide"
