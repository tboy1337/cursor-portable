"""Installer and publish script checks."""

from __future__ import annotations

import importlib.metadata
from io import BytesIO
from pathlib import Path
import subprocess
import tarfile

import pytest

from cursor_portable._version import (
    __version__,
    nearest_project_version,
    read_project_fields,
    read_stamp,
    resolve_version,
    write_version_stamp,
)
from cursor_portable.failure import Failure
import cursor_portable.install.acquire as acquire
from cursor_portable.install.acquire import copy_tool_package
from cursor_portable.install.apply import install_tree
from cursor_portable.models import (
    HostSystem,
    InstallChoices,
    InstallScope,
    ProductKind,
    ProfileMode,
)
from cursor_portable.repack.layout import StickLayout
from scripts.ci_pack import _copy_package, _github_headers, tkinter_support
from scripts.ci_publish import main as publish_main
from scripts.ci_release import (
    ReleaseDecision,
    choose_release,
    github_release_exists,
    main as release_main,
    missing_pyproject,
    show_pyproject,
)

_COMMIT = "a" * 40


def test_publish_creates_a_source_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, list[str]] = {}

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("scripts.ci_publish.subprocess.run", fake_run)
    notes = tmp_path / "notes.md"
    code = publish_main(
        ["--version", "1.2.3", "--target", _COMMIT, "--notes", str(notes)]
    )
    assert code == 0
    text = notes.read_text(encoding="utf-8")
    assert "cursor-portable 1.2.3." in text
    assert "Clone the repository" in text
    command = seen["command"]
    assert command[:4] == ["gh", "release", "create", "1.2.3"]
    assert "--target" in command
    assert _COMMIT in command
    assert str(notes) in command
    assert not any(part.endswith((".exe", ".7z", ".sha256")) for part in command)


def test_publish_refuses_a_bad_version(tmp_path: Path) -> None:
    assert (
        publish_main(
            ["--version", "1.2", "--target", _COMMIT, "--notes", str(tmp_path / "n.md")]
        )
        == 1
    )


def _pyproject(version: str) -> str:
    return "[project]\n" 'name = "cursor-portable"\n' f'version = "{version}"\n'


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _commit_pyproject(repo: Path, version: str, message: str) -> str:
    (repo / "pyproject.toml").write_text(_pyproject(version), encoding="utf-8")
    _git(repo, "add", "pyproject.toml")
    _git(
        repo,
        "-c",
        "commit.gpgsign=false",
        "-c",
        "user.name=cursor-portable",
        "-c",
        "user.email=cursor-portable@example.com",
        "commit",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def test_release_follows_only_a_version_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    previous = _commit_pyproject(repo, "1.0.0", "initial")
    _commit_pyproject(repo, "1.0.1", "bump")
    output = tmp_path / "github-output.txt"
    monkeypatch.chdir(repo)
    code = release_main(
        [
            "--before",
            previous,
            "--after-file",
            "pyproject.toml",
            "--github-output",
            str(output),
        ]
    )
    assert code == 0
    text = output.read_text(encoding="utf-8")
    assert "release=true" in text
    assert "version=1.0.1" in text

    def released(_version: str) -> bool:
        return True

    monkeypatch.setattr("scripts.ci_release.github_release_exists", released)
    same = tmp_path / "same.txt"
    head = _git(repo, "rev-parse", "HEAD")
    assert (
        release_main(
            [
                "--before",
                head,
                "--after-file",
                "pyproject.toml",
                "--github-output",
                str(same),
            ]
        )
        == 0
    )
    assert "release=false" in same.read_text(encoding="utf-8")

    def unreleased(_version: str) -> bool:
        return False

    monkeypatch.setattr("scripts.ci_release.github_release_exists", unreleased)
    missing_release = tmp_path / "missing-release.txt"
    assert (
        release_main(
            [
                "--before",
                head,
                "--after-file",
                "pyproject.toml",
                "--github-output",
                str(missing_release),
            ]
        )
        == 0
    )
    missing_text = missing_release.read_text(encoding="utf-8")
    assert "release=true" in missing_text
    assert "version=1.0.1" in missing_text
    (repo / "pyproject.toml").write_text(_pyproject("1.2"), encoding="utf-8")
    assert (
        release_main(
            [
                "--before",
                head,
                "--after-file",
                "pyproject.toml",
                "--github-output",
                str(tmp_path / "bad.txt"),
            ]
        )
        == 1
    )


def test_github_release_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes: dict[str, subprocess.CompletedProcess[str] | BaseException] = {
        "1.0.1": subprocess.CompletedProcess([], 1, "", "release not found"),
        "1.0.0": subprocess.CompletedProcess([], 0, '{"tagName":"1.0.0"}', ""),
        "slow": subprocess.TimeoutExpired(["gh"], 60),
        "absent": OSError("gh"),
        "9.9.9": subprocess.CompletedProcess([], 1, "", "auth required"),
    }

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == ["gh", "release", "view", command[3], "--json", "tagName"]
        outcome = outcomes[command[3]]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr("scripts.ci_release.subprocess.run", run)
    assert github_release_exists("1.0.1") is False
    assert github_release_exists("1.0.0") is True
    assert isinstance(github_release_exists("slow"), Failure)
    assert isinstance(github_release_exists("absent"), Failure)
    refused = github_release_exists("9.9.9")
    assert isinstance(refused, Failure)
    assert "auth required" in refused.message


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_packed_python_must_include_tkinter(tmp_path: Path) -> None:
    windows = tmp_path / "windows"
    _touch(windows / "Lib" / "tkinter" / "__init__.py")
    _touch(windows / "DLLs" / "_tkinter.pyd")
    assert tkinter_support(windows) is None

    library = tmp_path / "library"
    _touch(library / "lib" / "tkinter" / "__init__.py")
    _touch(library / "lib-dynload" / "_tkinter.so")
    assert tkinter_support(library) is None

    posix = tmp_path / "posix"
    _touch(posix / "lib" / "python3.14" / "tkinter" / "__init__.py")
    _touch(posix / "lib" / "python3.14" / "lib-dynload" / "_tkinter.so")
    assert tkinter_support(posix) is None

    (windows / "DLLs" / "_tkinter.pyd").unlink()
    without_extension = tkinter_support(windows)
    assert isinstance(without_extension, Failure)
    assert without_extension.code == "tkinter"
    extension_only = tmp_path / "extension"
    _touch(extension_only / "DLLs" / "_tkinter.pyd")
    assert isinstance(tkinter_support(extension_only), Failure)
    assert isinstance(tkinter_support(tmp_path / "absent"), Failure)


def test_github_pack_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert not _github_headers()
    monkeypatch.setenv("GH_TOKEN", "pack-token")
    headers = _github_headers()
    assert headers["Authorization"] == "Bearer pack-token"
    assert headers["Accept"] == "application/vnd.github+json"


def test_release_decision_edges(tmp_path: Path) -> None:
    assert missing_pyproject("fatal: path 'pyproject.toml' does not exist in 'abc'")
    assert missing_pyproject(
        "fatal: path 'pyproject.toml' exists on disk, but not in 'abc'"
    )
    assert missing_pyproject("fatal: bad object") is False
    unchanged = choose_release("1.0.0", "1.0.0", new_history=False)
    assert isinstance(unchanged, ReleaseDecision)
    assert unchanged.publish is False
    pending = choose_release(
        "1.0.0", "1.0.0", new_history=False, already_released=False
    )
    assert isinstance(pending, ReleaseDecision)
    assert pending.publish is True
    assert pending.version == "1.0.0"
    assert isinstance(
        choose_release("1.2", "1.2", new_history=False, already_released=False),
        Failure,
    )
    appeared = choose_release(None, "1.0.1", new_history=False)
    assert isinstance(appeared, ReleaseDecision)
    assert appeared.publish is True
    assert appeared.version == "1.0.1"
    first = choose_release(None, "1.0.0", new_history=True)
    assert isinstance(first, ReleaseDecision)
    assert first.publish is False
    assert isinstance(choose_release("1.0.0", "1.2", new_history=False), Failure)
    output = tmp_path / "out.txt"
    assert (
        release_main(
            [
                "--before",
                "0" * 40,
                "--after-file",
                str(tmp_path / "missing.toml"),
                "--github-output",
                str(output),
            ]
        )
        == 1
    )
    after = tmp_path / "pyproject.toml"
    after.write_text(_pyproject("1.2.3"), encoding="utf-8")
    fresh = tmp_path / "fresh.txt"
    assert (
        release_main(
            [
                "--before",
                "0" * 40,
                "--after-file",
                str(after),
                "--github-output",
                str(fresh),
            ]
        )
        == 0
    )
    assert "release=false" in fresh.read_text(encoding="utf-8")
    assert "version=1.2.3" in fresh.read_text(encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "README.md").write_text("notes\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(
        repo,
        "-c",
        "commit.gpgsign=false",
        "-c",
        "user.name=cursor-portable",
        "-c",
        "user.email=cursor-portable@example.com",
        "commit",
        "-m",
        "readme",
    )
    assert show_pyproject(_git(repo, "rev-parse", "HEAD"), cwd=repo) is None


def test_version_comes_only_from_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    name, version = read_project_fields(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert name == "cursor-portable"
    assert version == __version__
    with pytest.raises(ValueError, match="invalid"):
        read_project_fields("version = [\n")
    assert read_project_fields("[tool]\nname = 'x'\n") == (None, None)
    empty = tmp_path / "blank"
    empty.mkdir()
    (empty / "VERSION").write_text("\n", encoding="utf-8")
    assert read_stamp(empty) is None
    with pytest.raises(ValueError, match="empty"):
        write_version_stamp(empty, "  ")
    other = tmp_path / "pyproject.toml"
    other.write_text(
        '[project]\nname = "other"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    package = tmp_path / "cursor_portable"
    package.mkdir()
    assert nearest_project_version(package) is None
    assert resolve_version(package) == __version__

    def missing(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", missing)
    with pytest.raises(RuntimeError, match="VERSION stamp"):
        resolve_version(package)
    write_version_stamp(package, "2.0.0")
    assert read_stamp(package) == "2.0.0"
    assert resolve_version(package) == "2.0.0"
    project = tmp_path / "own"
    project.mkdir()
    (project / "pyproject.toml").write_text(_pyproject("3.4.5"), encoding="utf-8")
    nested = project / "cursor_portable"
    nested.mkdir()
    assert nearest_project_version(nested) == "3.4.5"
    write_version_stamp(nested, "8.0.0")
    assert resolve_version(nested) == "8.0.0"
    copied = tmp_path / "tool"
    assert copy_tool_package(copied) is None
    stamp = copied / "cursor_portable" / "VERSION"
    assert stamp.read_text(encoding="utf-8").strip() == __version__
    packed = tmp_path / "site"
    packed.mkdir()
    _copy_package(packed)
    assert (packed / "cursor_portable" / "VERSION").read_text(
        encoding="utf-8"
    ).strip() == (__version__)


def test_cached_python_from_another_minor_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def refuse(*_args: object, **_kwargs: object) -> Failure:
        calls.append("download")
        return Failure("python", "no")

    cached = tmp_path / "cached" / "python"
    cached.mkdir(parents=True)
    (cached / "python.exe").write_bytes(b"old")
    (cached / "python313.dll").write_bytes(b"old")
    (cached / "python3.dll").write_bytes(b"abi")
    monkeypatch.setattr(acquire, "_python_archive", refuse)
    assert isinstance(
        acquire.ensure_python(HostSystem.WINDOWS, tmp_path / "cached", arch="x64"),
        Failure,
    )
    assert calls == ["download"]
    assert (cached / "python313.dll").is_file()
    kept = tmp_path / "kept" / "python"
    kept.mkdir(parents=True)
    (kept / "python.exe").write_bytes(b"keep")
    (kept / "python314.dll").write_bytes(b"keep")
    assert (
        acquire.ensure_python(HostSystem.WINDOWS, tmp_path / "kept", arch="x64") is None
    )
    assert calls == ["download"]
    archive = tmp_path / "cpython-3.14.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        for name, body in (
            ("python/python.exe", b"new"),
            ("python/python314.dll", b"dll"),
        ):
            payload = BytesIO(body)
            info = tarfile.TarInfo(name)
            info.size = len(body)
            handle.addfile(info, payload)
    monkeypatch.setattr(acquire, "_python_archive", lambda *_args, **_kwargs: archive)
    assert (
        acquire.ensure_python(HostSystem.WINDOWS, tmp_path / "cached", arch="x64")
        is None
    )
    assert (cached / "python314.dll").read_bytes() == b"dll"
    assert not (cached / "python313.dll").exists()
    posix = tmp_path / "posix" / "python"
    (posix / "bin").mkdir(parents=True)
    (posix / "bin" / "python3").write_bytes(b"old")
    (posix / "lib" / "python3.13").mkdir(parents=True)
    monkeypatch.setattr(acquire, "_python_archive", refuse)
    assert isinstance(
        acquire.ensure_python(HostSystem.LINUX, tmp_path / "posix", arch="x64"),
        Failure,
    )


def test_install_replaces_python_from_another_minor(tmp_path: Path) -> None:
    app = tmp_path / "app" / "resources" / "app"
    app.mkdir(parents=True)
    (app / "product.json").write_text('{"version": "9"}', encoding="utf-8")
    source = tmp_path / "python-src"
    source.mkdir()
    (source / "python.exe").write_bytes(b"new")
    (source / "python314.dll").write_bytes(b"dll")
    layout = StickLayout.from_mount(tmp_path / "mount")
    layout.python.mkdir(parents=True)
    (layout.python / "python.exe").write_bytes(b"old")
    (layout.python / "python313.dll").write_bytes(b"old")
    (layout.python / "stale.txt").write_text("stale", encoding="utf-8")
    choices = InstallChoices(
        product=ProductKind.IDE,
        scope=InstallScope.USER,
        profile=ProfileMode.FRESH,
        volume_device="E:",
        typed_label="STICK",
    )
    assert (
        install_tree(
            tmp_path / "app",
            layout,
            choices,
            HostSystem.WINDOWS,
            sources=None,
            cursor_running=False,
            python_source=source,
        )
        is None
    )
    assert (layout.python / "python314.dll").read_bytes() == b"dll"
    assert not (layout.python / "python313.dll").exists()
    assert not (layout.python / "stale.txt").exists()
