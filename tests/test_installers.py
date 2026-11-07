"""Installer and publish script checks."""

from __future__ import annotations

import importlib.metadata
from io import BytesIO
import json
from pathlib import Path
import stat
import subprocess
import tarfile
import zipfile

import pytest

from cursor_portable._version import (
    __version__,
    nearest_project_version,
    read_project_fields,
    read_stamp,
    resolve_version,
    write_version_stamp,
)
from cursor_portable.download.http_client import HttpResponse
from cursor_portable.failure import Failure
from cursor_portable.git_runtime.extract import extract_git_archive
from cursor_portable.git_runtime.pin import (
    git_executable,
    parse_sha256_text,
    select_git_asset,
)
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
from scripts.ci_wheel import find_wheel, main as wheel_main

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
    assert "pip install cursor-portable" in text
    assert "Clone the repository" in text
    command = seen["command"]
    assert command[:4] == ["gh", "release", "create", "1.2.3"]
    assert "--target" in command
    assert _COMMIT in command
    assert str(notes) in command
    assert not any(part.endswith((".exe", ".7z", ".sha256")) for part in command)


def _wheel(dist: Path, name: str, payload: bytes = b"wheel") -> None:
    dist.mkdir(parents=True, exist_ok=True)
    (dist / name).write_bytes(payload)


def test_wheel_check_accepts_the_release_wheel(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _wheel(dist, "cursor_portable-1.2.3-py3-none-any.whl")
    _wheel(dist, "cursor_portable-1.2.30-py3-none-any.whl", b"other")
    (dist / "cursor_portable-1.2.3.tar.gz").write_bytes(b"sdist")
    assert wheel_main(["--version", "1.2.3", "--dist", str(dist)]) == 0
    found = find_wheel(dist, "1.2.3")
    assert isinstance(found, Path)
    assert found.name == "cursor_portable-1.2.3-py3-none-any.whl"


def test_wheel_check_refuses_a_bad_dist(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    assert wheel_main(["--version", "1.2", "--dist", str(dist)]) == 1
    assert wheel_main(["--version", "1.2.3", "--dist", str(dist)]) == 1
    _wheel(dist, "cursor_portable-1.2.3-py3-none-any.whl", b"")
    assert isinstance(find_wheel(dist, "1.2.3"), Failure)
    _wheel(dist, "cursor_portable-1.2.3-py3-none-any.whl")
    _wheel(dist, "cursor_portable-1.2.3-py311-none-any.whl")
    assert wheel_main(["--version", "1.2.3", "--dist", str(dist)]) == 1
    assert isinstance(
        find_wheel(dist / "cursor_portable-1.2.3-py3-none-any.whl", "1.2.3"),
        Failure,
    )


def test_wheel_check_reports_an_unreadable_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    _wheel(dist, "cursor_portable-1.2.3-py3-none-any.whl")

    def explode(_path: Path) -> int:
        raise OSError("denied")

    monkeypatch.setattr("scripts.ci_wheel._size", explode)
    refused = find_wheel(dist, "1.2.3")
    assert isinstance(refused, Failure)
    assert refused.code == "wheel"
    assert "denied" in refused.message


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


def test_select_git_asset_skips_busybox_and_hash_files() -> None:
    names = (
        "MinGit-2.9.0-64-bit.zip",
        "MinGit-2.10.0-64-bit.zip",
        "MinGit-2.10.0-busybox-64-bit.zip",
        "MinGit-2.10.0-32-bit.zip",
        "MinGit-2.10.0-arm64.zip",
        "dugite-native-v2.55.0-aaaa-macOS-x64.tar.gz",
        "dugite-native-v2.56.0-bbbb-macOS-x64.tar.gz",
        "dugite-native-v2.56.0-bbbb-macOS-x64.tar.gz.sha256",
        "dugite-native-v2.56.0-bbbb-ubuntu-arm.tar.gz",
        "dugite-native-v2.56.0-bbbb-ubuntu-arm64.tar.gz",
        "dugite-native-v2.56.0-bbbb-ubuntu-x64.lzma",
    )
    assert (
        select_git_asset(names, HostSystem.WINDOWS, "x64") == "MinGit-2.10.0-64-bit.zip"
    )
    assert select_git_asset(names, HostSystem.WINDOWS, "arm64") == (
        "MinGit-2.10.0-arm64.zip"
    )
    assert select_git_asset(names, HostSystem.MACOS, "x64") == (
        "dugite-native-v2.56.0-bbbb-macOS-x64.tar.gz"
    )
    assert select_git_asset(names, HostSystem.LINUX, "arm64") == (
        "dugite-native-v2.56.0-bbbb-ubuntu-arm64.tar.gz"
    )
    assert select_git_asset(names, HostSystem.LINUX, "x64") is None
    assert select_git_asset(names, HostSystem.WINDOWS, "sparc") is None
    assert select_git_asset(
        ("MinGit-2.beta-64-bit.zip", "MinGit-2.10.0-64-bit.zip"),
        HostSystem.WINDOWS,
        "x64",
    ) == ("MinGit-2.10.0-64-bit.zip")
    assert git_executable(Path("git"), HostSystem.WINDOWS).name == "git.exe"
    assert git_executable(Path("git"), HostSystem.LINUX).name == "git"
    assert parse_sha256_text("") is None
    assert parse_sha256_text("not-a-hash") is None
    assert parse_sha256_text(f"{'ab' * 32}  git.tar.gz") == "ab" * 32


def test_git_sha256_uses_digest_then_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "dugite-native-v2.56.0-bbbb-ubuntu-x64.tar.gz"
    digest = "cd" * 32
    calls: list[str] = []

    def refuse(*_args: object, **_kwargs: object) -> Failure:
        calls.append("download")
        return Failure("http", "no")

    monkeypatch.setattr(acquire, "download_https", refuse)
    published = {
        "assets": [
            {
                "name": name,
                "browser_download_url": "https://example.invalid/git.tar.gz",
                "digest": f"sha256:{digest}",
            }
        ]
    }
    assert acquire._git_sha256(published, name, tmp_path) == digest
    assert not calls

    def write_hash(_url: str, destination: Path, **_kwargs: object) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(f"{'ab' * 32}\n", encoding="utf-8")

    monkeypatch.setattr(acquire, "download_https", write_hash)
    sibling = {
        "assets": [
            {
                "name": name,
                "browser_download_url": "https://example.invalid/git.tar.gz",
            },
            {
                "name": f"{name}.sha256",
                "browser_download_url": "https://example.invalid/git.tar.gz.sha256",
            },
        ]
    }
    assert acquire._git_sha256(sibling, name, tmp_path / "sibling") == "ab" * 32
    assert isinstance(acquire._git_sha256({"assets": []}, name, tmp_path), Failure)


def test_git_archive_selects_the_asset_for_the_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows = "MinGit-2.56.0.4-64-bit.zip"
    linux = "dugite-native-v2.56.0-bbbb-ubuntu-x64.tar.gz"
    digest = "ab" * 32
    assets = [
        {
            "name": asset_name,
            "browser_download_url": f"https://example.invalid/{asset_name}",
            "digest": f"sha256:{digest}",
        }
        for asset_name in (windows, "MinGit-2.56.0.4-busybox-64-bit.zip", linux)
    ]
    body = json.dumps({"assets": assets}).encode("utf-8")

    def fake_get(_self: object, url: str, *, timeout: float) -> HttpResponse:
        del timeout
        return HttpResponse(status=200, url=url, body=body, headers={})

    seen: list[str] = []

    def fake_download(url: str, destination: Path, **_kwargs: object) -> None:
        seen.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"archive")

    monkeypatch.setattr(acquire.UrllibClient, "get", fake_get)
    monkeypatch.setattr(acquire, "download_https", fake_download)
    monkeypatch.setattr(acquire, "check_download", lambda *_args, **_kwargs: None)
    saved = acquire._git_archive(HostSystem.WINDOWS, "x64", tmp_path / "win")
    assert isinstance(saved, Path)
    assert saved.name == windows
    assert "busybox" not in seen[-1]
    posix = acquire._git_archive(HostSystem.LINUX, "x64", tmp_path / "linux")
    assert isinstance(posix, Path)
    assert posix.name == linux
    assert isinstance(
        acquire._git_archive(HostSystem.WINDOWS, "sparc", tmp_path / "bad"),
        Failure,
    )


def test_ensure_git_keeps_a_tree_and_replaces_a_broken_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def archive(*_args: object, **_kwargs: object) -> Path:
        calls.append("download")
        path = tmp_path / "MinGit-2.56.0.4-64-bit.zip"
        with zipfile.ZipFile(path, "w") as handle:
            handle.writestr("cmd/git.exe", b"git")
        return path

    monkeypatch.setattr(acquire, "_git_archive", archive)
    broken = tmp_path / "cached" / "git"
    broken.mkdir(parents=True)
    (broken / "stale.txt").write_text("stale", encoding="utf-8")
    assert (
        acquire.ensure_git(HostSystem.WINDOWS, tmp_path / "cached", arch="x64") is None
    )
    assert (broken / "cmd" / "git.exe").read_bytes() == b"git"
    assert not (broken / "stale.txt").exists()
    assert calls == ["download"]
    assert (
        acquire.ensure_git(HostSystem.WINDOWS, tmp_path / "cached", arch="x64") is None
    )
    assert calls == ["download"]
    kept = tmp_path / "kept" / "git" / "bin"
    kept.mkdir(parents=True)
    (kept / "git").write_bytes(b"keep")
    assert acquire.ensure_git(HostSystem.LINUX, tmp_path / "kept", arch="x64") is None
    assert calls == ["download"]
    assert (kept / "git").read_bytes() == b"keep"


def test_extract_git_archive_materializes_links(tmp_path: Path) -> None:
    archive = tmp_path / "git.tar"
    payload = b"git-bytes"
    with tarfile.open(archive, "w") as handle:
        info = tarfile.TarInfo("libexec/git-core/git")
        info.size = len(payload)
        info.mode = 0o755
        handle.addfile(info, BytesIO(payload))
        link = tarfile.TarInfo("bin/git")
        link.type = tarfile.SYMTYPE
        link.linkname = "../libexec/git-core/git"
        handle.addfile(link)
        hard = tarfile.TarInfo("bin/git-receive-pack")
        hard.type = tarfile.LNKTYPE
        hard.linkname = "libexec/git-core/git"
        handle.addfile(hard)
    destination = tmp_path / "out"
    assert extract_git_archive(archive, destination) is None
    copied = destination / "bin" / "git"
    assert copied.read_bytes() == payload
    assert not copied.is_symlink()
    assert (destination / "bin" / "git-receive-pack").read_bytes() == payload


def test_extract_git_archive_refuses_escape(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as handle:
        info = tarfile.TarInfo("../evil.txt")
        info.size = 2
        handle.addfile(info, BytesIO(b"no"))
    assert isinstance(extract_git_archive(archive, tmp_path / "out"), Failure)
    assert not (tmp_path / "evil.txt").exists()
    zipped = tmp_path / "bad.zip"
    with zipfile.ZipFile(zipped, "w") as handle:
        handle.writestr("../nope.txt", b"x")
    assert isinstance(extract_git_archive(zipped, tmp_path / "zip-out"), Failure)
    assert not (tmp_path / "nope.txt").exists()


def _tar_file(name: str, payload: bytes = b"git") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = 0o755
    return info


def _add_bytes(handle: tarfile.TarFile, name: str, payload: bytes = b"git") -> None:
    handle.addfile(_tar_file(name, payload), BytesIO(payload))


def test_extract_git_archive_covers_links_and_refusals(tmp_path: Path) -> None:
    archive = tmp_path / "links.tar"
    with tarfile.open(archive, "w") as handle:
        handle.addfile(tarfile.TarInfo("./"))
        directory = tarfile.TarInfo("real")
        directory.type = tarfile.DIRTYPE
        handle.addfile(directory)
        _add_bytes(handle, "libexec/git-core/git")
        directory_link = tarfile.TarInfo("alias")
        directory_link.type = tarfile.SYMTYPE
        directory_link.linkname = "real"
        handle.addfile(directory_link)
    destination = tmp_path / "links"
    assert extract_git_archive(archive, destination) is None
    assert (destination / "alias").is_dir()
    assert (destination / "libexec" / "git-core" / "git").read_bytes() == b"git"

    absolute = tmp_path / "absolute.tar"
    with tarfile.open(absolute, "w") as handle:
        _add_bytes(handle, "/etc/passwd")
    assert isinstance(extract_git_archive(absolute, tmp_path / "absolute"), Failure)

    drive = tmp_path / "drive.tar"
    with tarfile.open(drive, "w") as handle:
        _add_bytes(handle, "C:/Windows/git.exe")
    assert isinstance(extract_git_archive(drive, tmp_path / "drive"), Failure)

    escaped = tmp_path / "escape-link.tar"
    with tarfile.open(escaped, "w") as handle:
        link = tarfile.TarInfo("bin/git")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        handle.addfile(link)
    assert isinstance(extract_git_archive(escaped, tmp_path / "escape-link"), Failure)

    missing = tmp_path / "missing.tar"
    with tarfile.open(missing, "w") as handle:
        link = tarfile.TarInfo("bin/git")
        link.type = tarfile.SYMTYPE
        link.linkname = "missing-target"
        handle.addfile(link)
    assert isinstance(extract_git_archive(missing, tmp_path / "missing"), Failure)

    cyclic = tmp_path / "cycle.tar"
    with tarfile.open(cyclic, "w") as handle:
        first = tarfile.TarInfo("a")
        first.type = tarfile.SYMTYPE
        first.linkname = "b"
        second = tarfile.TarInfo("b")
        second.type = tarfile.SYMTYPE
        second.linkname = "a"
        handle.addfile(first)
        handle.addfile(second)
    assert isinstance(extract_git_archive(cyclic, tmp_path / "cycle"), Failure)

    other = tmp_path / "fifo.tar"
    with tarfile.open(other, "w") as handle:
        fifo = tarfile.TarInfo("bin/git")
        fifo.type = tarfile.FIFOTYPE
        handle.addfile(fifo)
    assert isinstance(extract_git_archive(other, tmp_path / "fifo"), Failure)

    long = tmp_path / "long.tar"
    with tarfile.open(long, "w") as handle:
        for index in range(33):
            link = tarfile.TarInfo(f"link-{index}")
            link.type = tarfile.SYMTYPE
            link.linkname = f"link-{index + 1}"
            handle.addfile(link)
    assert isinstance(extract_git_archive(long, tmp_path / "long"), Failure)

    broken = tmp_path / "broken.tar"
    broken.write_bytes(b"not-a-tar")
    assert isinstance(extract_git_archive(broken, tmp_path / "broken"), Failure)

    linked = tmp_path / "zip-link.zip"
    with zipfile.ZipFile(linked, "w") as handle:
        handle.writestr("libexec/git-core/git", b"zip-git")
        info = zipfile.ZipInfo("bin/git")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        handle.writestr(info, b"../libexec/git-core/git")
        empty = zipfile.ZipInfo("bin/empty")
        empty.external_attr = stat.S_IFLNK << 16
        handle.writestr(empty, b"")
    assert isinstance(extract_git_archive(linked, tmp_path / "zip-link"), Failure)
    copied = tmp_path / "zip-link-ok.zip"
    with zipfile.ZipFile(copied, "w") as handle:
        handle.mkdir("cmd")
        info = zipfile.ZipInfo("cmd/git.exe")
        info.external_attr = 0o755 << 16
        handle.writestr(info, b"git")
        zip_link = zipfile.ZipInfo("git.exe")
        zip_link.external_attr = (stat.S_IFLNK | 0o777) << 16
        handle.writestr(zip_link, b"cmd/git.exe")
    assert extract_git_archive(copied, tmp_path / "zip-ok") is None
    assert (tmp_path / "zip-ok" / "git.exe").read_bytes() == b"git"
    assert (tmp_path / "zip-ok" / "cmd").is_dir()


def test_install_replaces_git_without_an_executable(tmp_path: Path) -> None:
    app = tmp_path / "app" / "resources" / "app"
    app.mkdir(parents=True)
    (app / "product.json").write_text('{"version": "9"}', encoding="utf-8")
    source = tmp_path / "git-src"
    (source / "cmd").mkdir(parents=True)
    (source / "cmd" / "git.exe").write_bytes(b"git")
    layout = StickLayout.from_mount(tmp_path / "mount")
    layout.git.mkdir(parents=True)
    (layout.git / "stale.txt").write_text("stale", encoding="utf-8")
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
            git_source=source,
        )
        is None
    )
    assert (layout.git / "cmd" / "git.exe").read_bytes() == b"git"
    assert not (layout.git / "stale.txt").exists()
