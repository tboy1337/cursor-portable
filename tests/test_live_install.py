"""Live install downloads and Windows elevation prompts."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
import zipfile

from cursor_portable.disk.commands import RepairMode, format_plan, repair_plan
from cursor_portable.disk.filesystem import (
    display_filesystem,
    host_can_format,
    label_limit,
    probed_filesystem,
)
import cursor_portable.disk.spawn as spawn
from cursor_portable.disk.spawn import _windows_tool, capture_command, quote_windows_arg
from cursor_portable.disk.volume import Volume
from cursor_portable.download.fetch import (
    _content_length,
    download_https,
    download_progress_message,
)
from cursor_portable.download.http_client import HttpResponse
from cursor_portable.download.resolve import ResolvedDownload
from cursor_portable.failure import Failure
import cursor_portable.install.acquire as acquire
from cursor_portable.install.acquire import (
    _extract_windows_installer,
    host_arch,
    ide_platform_id,
    payload_cache,
)
from cursor_portable.install.apply import install_tree
import cursor_portable.launcher.path_store as path_store
from cursor_portable.launcher.path_store import broadcast_environment
import cursor_portable.launcher.processes as processes
from cursor_portable.launcher.processes import pid_alive
from cursor_portable.models import (
    HostSystem,
    InstallChoices,
    InstallScope,
    ProductKind,
    ProfileMode,
)
import cursor_portable.repack.copy_tree as copy_tree
from cursor_portable.repack.layout import StickLayout
from cursor_portable.repack.meta import read_stick_identity
from cursor_portable.wizard.flow import DriveDisposition, classify_drive


def test_unlabeled_windows_command_asks_for_approval(
    monkeypatch: object,
) -> None:
    seen: dict[str, object] = {}

    def fake_shell(
        executable: str,
        parameters: str,
        directory: Path,
        verb: str,
    ) -> int:
        seen["verb"] = verb
        seen["executable"] = executable
        seen["parameters"] = parameters
        payload = json.loads((directory / "command.json").read_text(encoding="utf-8"))
        seen["payload"] = payload
        (directory / "output.txt").write_text("done\n", encoding="utf-8")
        (directory / "code.txt").write_text("0", encoding="ascii")
        return 0

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.disk.spawn.elevation_ready",
        lambda _system: False,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.disk.spawn.is_windows",
        lambda: True,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.disk.spawn.host_system_name",
        lambda: HostSystem.WINDOWS,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.disk.spawn._shell_execute",
        fake_shell,
    )
    captured = capture_command(("format.com", "E:", "/Y"), elevate=True)
    assert isinstance(captured, tuple)
    assert captured[0] == 0
    assert "done" in captured[1]
    assert seen["verb"] == "runas"
    assert "powershell" in str(seen["executable"]).casefold()
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert str(payload["executable"]).casefold().endswith("format.com")
    assert "E:" in str(payload["arguments"])
    assert quote_windows_arg("E:") == "E:"
    assert quote_windows_arg("a b") == '"a b"'
    assert quote_windows_arg('a"b') == '"a\\"b"'
    assert quote_windows_arg("") == '""'

    def cancelled(*_args: object) -> Failure:
        return Failure("elevation", "Elevation was cancelled. Nothing was changed.")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.disk.spawn._shell_execute",
        cancelled,
    )
    refused = capture_command(("format.com", "E:"), elevate=True)
    assert isinstance(refused, Failure)
    assert refused.code == "elevation"


def test_download_refuses_plain_http(tmp_path: Path) -> None:
    refused = download_https(
        "http://example.invalid/cursor",
        tmp_path / "cursor",
        minimum_bytes=1,
    )
    assert isinstance(refused, Failure)


def test_platform_id_for_this_computer(monkeypatch: object) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.install.acquire.platform.machine",
        lambda: "AMD64",
    )
    assert host_arch() == "x64"
    assert (
        ide_platform_id(HostSystem.WINDOWS, "x64", InstallScope.USER)
        == "win32-x64-user"
    )
    assert (
        ide_platform_id(HostSystem.LINUX, "arm64", InstallScope.USER) == "linux-arm64"
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.install.acquire.platform.machine",
        lambda: "riscv",
    )
    assert isinstance(host_arch(), Failure)
    assert isinstance(
        ide_platform_id(HostSystem.WINDOWS, "ppc", InstallScope.USER), Failure
    )
    assert (
        ide_platform_id(HostSystem.MACOS, "arm64", InstallScope.USER) == "darwin-arm64"
    )


def _python_tar(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as handle:
        data = b"py"
        info = tarfile.TarInfo("python/python.exe")
        info.size = len(data)
        handle.addfile(info, BytesIO(data))


def test_acquire_payload_without_network(tmp_path: Path, monkeypatch: object) -> None:
    def fake_get(self: object, url: str, *, timeout: float) -> HttpResponse | Failure:
        del self, timeout
        if "python-build-standalone" in url:
            body = json.dumps(
                {
                    "assets": [
                        {
                            "name": (
                                "cpython-3.13.1-x86_64-pc-windows-msvc-install_only.tar.gz"
                            ),
                            "browser_download_url": "https://example.invalid/python.tar.gz",
                            "digest": "sha256:" + ("ab" * 32),
                        }
                    ]
                }
            ).encode("utf-8")
            return HttpResponse(status=200, url=url, body=body, headers={})
        if "install" in url:
            return HttpResponse(
                status=200,
                url=url,
                body=b"https://downloads.cursor.com/cli.zip",
                headers={},
            )
        return Failure("http", url)

    def fake_download(url: str, destination: Path, **_kwargs: object) -> None:
        del url
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.name == "innoextract670.zip":
            with zipfile.ZipFile(destination, "w") as handle:
                handle.writestr("innoextract.exe", b"exe")
                handle.writestr("libbz2-1.dll", b"dll")
            return
        if destination.name.endswith(".tar.gz"):
            _python_tar(destination)
            return
        if destination.name == "cli-archive":
            with zipfile.ZipFile(destination, "w") as handle:
                handle.writestr("cursor.exe", b"cli")
            return
        destination.write_bytes(b"installer")

    def fake_run(command: list[str], **_kwargs: object) -> None:
        output = Path(command[command.index("--output-dir") + 1])
        app = output / "resources" / "app"
        app.mkdir(parents=True, exist_ok=True)
        (app / "product.json").write_text("{}\n", encoding="utf-8")
        (output / "Cursor.exe").write_text("cursor", encoding="utf-8")

    def resolved(
        platform_id: str, _client: object, timeout: float = 60.0
    ) -> ResolvedDownload:
        del timeout
        return ResolvedDownload(
            platform_id,
            "https://downloads.cursor.com/CursorSetup.exe",
            "CursorSetup.exe",
        )

    monkeypatch.setattr(acquire.platform, "machine", lambda: "AMD64")  # type: ignore[attr-defined]
    monkeypatch.setattr(acquire.UrllibClient, "get", fake_get)  # type: ignore[attr-defined]
    monkeypatch.setattr(acquire, "download_https", fake_download)  # type: ignore[attr-defined]
    monkeypatch.setattr(acquire, "check_download", lambda *_args, **_kwargs: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(acquire, "resolve_ide", resolved)  # type: ignore[attr-defined]
    monkeypatch.setattr(acquire, "run_command", fake_run)  # type: ignore[attr-defined]
    monkeypatch.setattr(acquire.shutil, "which", lambda _name: None)  # type: ignore[attr-defined]
    messages: list[str] = []
    choices = InstallChoices(
        product=ProductKind.BOTH,
        scope=InstallScope.USER,
        profile=ProfileMode.FRESH,
        volume_device="E:",
        typed_label="E:",
    )
    ready = acquire.acquire_payload(
        choices,
        HostSystem.WINDOWS,
        tmp_path / "payload",
        progress=messages.append,
    )
    assert ready is None
    assert (tmp_path / "payload" / "ide-user" / "Cursor.exe").is_file()
    assert (tmp_path / "payload" / "python" / "python.exe").is_file()
    assert (tmp_path / "payload" / "cli" / "cursor.exe").is_file()
    assert (tmp_path / "payload" / "tool" / "cursor_portable" / "__init__.py").is_file()
    assert messages
    assert (
        acquire.acquire_payload(choices, HostSystem.WINDOWS, tmp_path / "payload")
        is None
    )

    def broken(*_args: object, **_kwargs: object) -> None:
        raise OSError("full")

    monkeypatch.setattr(acquire.shutil, "copytree", broken)  # type: ignore[attr-defined]
    assert isinstance(acquire.copy_tool_package(tmp_path / "broken-tool"), Failure)
    bad_zip = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad_zip, "w") as handle:
        handle.writestr("../evil.txt", "no")
    assert isinstance(
        acquire._extract_cli_archive(bad_zip, tmp_path / "cli-bad"), Failure
    )
    assert isinstance(
        acquire._extract_installer(
            HostSystem.LINUX,
            tmp_path / "Cursor.AppImage",
            tmp_path / "linux-app",
            tmp_path / "tools",
        ),
        Failure,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        acquire.shutil,
        "which",
        lambda name: "C:/tools/innoextract.exe" if name == "innoextract" else None,
    )
    assert (
        acquire.innoextract_executable(tmp_path / "tools") == "C:/tools/innoextract.exe"
    )
    assert isinstance(
        acquire._extract_innoextract_zip(tmp_path / "not-a-zip", tmp_path / "tools"),
        Failure,
    )
    empty = tmp_path / "empty-python"
    empty.mkdir()
    assert isinstance(acquire._python_root(empty), Failure)


def test_download_progress_names_the_file_and_total() -> None:
    assert (
        download_progress_message(
            16 * 1024 * 1024,
            86 * 1024 * 1024,
            label="the Cursor CLI",
        )
        == "Downloading the Cursor CLI: 16 MB of 86 MB"
    )
    assert download_progress_message(8 * 1024 * 1024, None) == "Downloaded 8 MB"
    assert download_progress_message(2048, None) == "Downloaded 2 KB"
    assert download_progress_message(12, None, label="notes") == (
        "Downloading notes: 12 bytes"
    )


def test_content_length_reads_the_response_header() -> None:
    class _Headers:
        def __init__(self, value: object) -> None:
            self.value = value

        def get(self, _key: str) -> object:
            return self.value

    assert _content_length(SimpleNamespace(headers=_Headers("20"))) == 20
    assert _content_length(SimpleNamespace(headers=_Headers(20))) == 20
    assert _content_length(SimpleNamespace(headers=_Headers("no"))) is None
    assert _content_length(SimpleNamespace()) is None


def test_payload_cache_and_stream_download(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("CURSOR_PORTABLE_CACHE", str(tmp_path / "cache"))  # type: ignore[attr-defined]
    assert payload_cache() == tmp_path / "cache"
    monkeypatch.delenv("CURSOR_PORTABLE_CACHE", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("LOCALAPPDATA", raising=False)  # type: ignore[attr-defined]
    monkeypatch.setattr(acquire.Path, "home", lambda: tmp_path)  # type: ignore[attr-defined]
    assert payload_cache() == tmp_path / ".cache" / "cursor-portable" / "payload"

    class _Body:
        def __init__(self, data: bytes, url: str) -> None:
            self.data = data
            self.url = url
            self.pos = 0

        def read(self, size: int) -> bytes:
            chunk = self.data[self.pos : self.pos + size]
            self.pos += size
            return chunk

        def geturl(self) -> str:
            return self.url

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    notes: list[str] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.download.fetch.urlopen",
        lambda *_args, **_kwargs: _Body(
            b"x" * (8 * 1024 * 1024 + 1),
            "https://example.invalid/file",
        ),
    )
    saved = tmp_path / "cursor.bin"
    assert (
        download_https(
            "https://example.invalid/file",
            saved,
            minimum_bytes=10,
            progress=notes.append,
        )
        is None
    )
    assert notes
    assert (
        download_https("https://example.invalid/file", saved, minimum_bytes=10) is None
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.download.fetch.urlopen",
        lambda *_args, **_kwargs: _Body(b"x" * 20, "http://example.invalid/file"),
    )
    redirected = download_https(
        "https://example.invalid/file",
        tmp_path / "redirected.bin",
        minimum_bytes=1,
    )
    assert isinstance(redirected, Failure)

    def broken(*_args: object, **_kwargs: object) -> None:
        raise URLError("down")

    monkeypatch.setattr("cursor_portable.download.fetch.urlopen", broken)  # type: ignore[attr-defined]
    failed = download_https(
        "https://example.invalid/file",
        tmp_path / "missing.bin",
        minimum_bytes=1,
    )
    assert isinstance(failed, Failure)

    def http_fail(*_args: object, **_kwargs: object) -> None:
        raise HTTPError(
            "https://example.invalid/file",
            404,
            "missing",
            hdrs=None,  # type: ignore[arg-type]
            fp=BytesIO(b""),
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.download.fetch.urlopen",
        http_fail,
    )
    assert isinstance(
        download_https(
            "https://example.invalid/file",
            tmp_path / "http.bin",
            minimum_bytes=1,
        ),
        Failure,
    )


def test_shell_execute_reports_cancel(monkeypatch: object) -> None:
    class _Call:
        def __init__(self, func: object) -> None:
            self.func = func
            self.argtypes = None
            self.restype = None

        def __call__(self, *args: object) -> object:
            return self.func(*args)  # type: ignore[operator]

    def execute(info: object) -> bool:
        target = getattr(info, "_obj")
        target.hProcess = 1
        return True

    def exit_code(_handle: object, code: object) -> bool:
        getattr(code, "_obj").value = 7
        return True

    def library(name: str, use_last_error: bool = False) -> object:
        del use_last_error
        if name == "shell32":
            return SimpleNamespace(ShellExecuteExW=_Call(execute))
        return SimpleNamespace(
            WaitForSingleObject=_Call(lambda *_args: 0),
            GetExitCodeProcess=_Call(exit_code),
            CloseHandle=_Call(lambda *_args: True),
        )

    monkeypatch.setattr(  # type: ignore[attr-defined]
        spawn.ctypes, "WinDLL", library, raising=False
    )
    assert spawn._shell_execute("powershell.exe", "", Path("."), "runas") == 7

    def cancelled(_info: object) -> bool:
        return False

    monkeypatch.setattr(  # type: ignore[attr-defined]
        spawn.ctypes, "get_last_error", lambda: 1223, raising=False
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        spawn.ctypes,
        "WinDLL",
        lambda name, use_last_error=False: SimpleNamespace(
            ShellExecuteExW=_Call(cancelled)
        ),
        raising=False,
    )
    refused = spawn._shell_execute("powershell.exe", "", Path("."), "runas")
    assert isinstance(refused, Failure)
    assert "cancelled" in refused.message
    missing = spawn._read_elevated_result(Path("missing-out"), Path("missing-code"), 5)
    assert isinstance(missing, Failure)


def test_acquire_reports_each_download_failure(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    patch = monkeypatch.setattr  # type: ignore[attr-defined]
    patch(acquire.platform, "machine", lambda: "aarch64")
    assert host_arch() == "arm64"
    choices = InstallChoices(
        ProductKind.IDE,
        InstallScope.USER,
        ProfileMode.FRESH,
        "E:",
        "E:",
        target_arch="",
    )
    patch(acquire, "host_arch", lambda: Failure("arch", "no"))
    assert isinstance(
        acquire.ensure_python(HostSystem.WINDOWS, tmp_path / "a"), Failure
    )
    assert isinstance(
        acquire.ensure_ide(choices, HostSystem.WINDOWS, tmp_path / "b"), Failure
    )
    patch(acquire, "host_arch", lambda: "x64")
    patch(acquire, "copy_tool_package", lambda _path: None)
    patch(acquire, "ensure_python", lambda *_args, **_kwargs: None)
    patch(
        acquire,
        "ensure_ide",
        lambda *_args, **_kwargs: Failure("ide", "no"),
    )
    assert isinstance(
        acquire.acquire_payload(choices, HostSystem.WINDOWS, tmp_path / "c"),
        Failure,
    )
    cli = InstallChoices(
        ProductKind.CLI,
        InstallScope.USER,
        ProfileMode.FRESH,
        "E:",
        "E:",
    )
    patch(acquire, "ensure_cli", lambda *_args, **_kwargs: None)
    assert acquire.acquire_payload(cli, HostSystem.WINDOWS, tmp_path / "d") is None


def test_python_and_cli_archives_refuse_bad_files(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    patch = monkeypatch.setattr  # type: ignore[attr-defined]
    patch(acquire, "host_arch", lambda: "x64")
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"not-a-tar")
    patch(acquire, "_python_archive", lambda *_args, **_kwargs: bad)
    assert isinstance(
        acquire.ensure_python(HostSystem.LINUX, tmp_path / "bad"), Failure
    )
    empty = tmp_path / "empty.tar.gz"
    with tarfile.open(empty, "w:gz") as handle:
        handle.addfile(tarfile.TarInfo("readme.txt"), BytesIO(b"none"))
    patch(acquire, "_python_archive", lambda *_args, **_kwargs: empty)
    assert isinstance(
        acquire.ensure_python(HostSystem.LINUX, tmp_path / "empty"), Failure
    )
    root = tmp_path / "layout"
    binary = root / "python" / "bin" / "python3"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"py")
    assert acquire._python_root(root) == root / "python"
    ready = tmp_path / "ready" / "python" / "bin"
    ready.mkdir(parents=True)
    (ready / "python3").write_bytes(b"py")
    assert acquire.ensure_python(HostSystem.LINUX, tmp_path / "ready") is None
    patch(
        acquire.UrllibClient,
        "get",
        lambda *_args, **_kwargs: Failure("http", "offline"),
    )
    assert isinstance(acquire.ensure_cli(HostSystem.WINDOWS, tmp_path / "cli"), Failure)
    patch(
        acquire.UrllibClient,
        "get",
        lambda *_args, **_kwargs: HttpResponse(
            200, "https://cursor.com/install", b"no urls", {}
        ),
    )
    assert isinstance(
        acquire.ensure_cli(HostSystem.WINDOWS, tmp_path / "cli2"), Failure
    )
    archive = tmp_path / "cli.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        payload = BytesIO(b"cursor")
        info = tarfile.TarInfo("cursor")
        info.size = 6
        handle.addfile(info, payload)
    folder = tmp_path / "cli-out"
    folder.mkdir()
    assert acquire._extract_cli_archive(archive, folder) is None
    assert isinstance(acquire._extract_cli_archive(tmp_path, folder), Failure)


def test_release_lookup_refuses_incomplete_metadata(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    patch = monkeypatch.setattr  # type: ignore[attr-defined]
    choices = InstallChoices(
        ProductKind.IDE,
        InstallScope.USER,
        ProfileMode.FRESH,
        "E:",
        "E:",
    )
    patch(acquire, "host_arch", lambda: "x64")
    patch(acquire, "resolve_ide", lambda *_args, **_kwargs: Failure("http", "no"))
    assert isinstance(
        acquire.ensure_ide(choices, HostSystem.WINDOWS, tmp_path / "ide"), Failure
    )
    resolved = ResolvedDownload(
        "win32-x64-user", "https://downloads.cursor.com/Cursor.exe", "Cursor.exe"
    )
    patch(acquire, "resolve_ide", lambda *_args, **_kwargs: resolved)
    patch(acquire, "download_https", lambda *_args, **_kwargs: Failure("http", "no"))
    assert isinstance(
        acquire.ensure_ide(choices, HostSystem.WINDOWS, tmp_path / "ide2"), Failure
    )
    patch(acquire, "download_https", lambda *_args, **_kwargs: None)
    patch(
        acquire,
        "_extract_installer",
        lambda *_args, **_kwargs: Failure("command", "no"),
    )
    assert isinstance(
        acquire.ensure_ide(choices, HostSystem.WINDOWS, tmp_path / "ide3"), Failure
    )
    patch(acquire, "_extract_installer", lambda *_args, **_kwargs: None)
    assert isinstance(
        acquire.ensure_ide(choices, HostSystem.LINUX, tmp_path / "ide4"), Failure
    )
    patch(acquire, "_seven_zip", lambda: "7z")
    patch(acquire, "extract_ide", lambda *_args, **_kwargs: None)
    installer = tmp_path / "Cursor.dmg"
    installer.write_bytes(b"dmg")
    assert (
        acquire._extract_installer(
            HostSystem.LINUX, installer, tmp_path / "out", tmp_path / "tools"
        )
        is None
    )
    patch(acquire, "download_https", lambda *_args, **_kwargs: Failure("http", "no"))
    patch(acquire.shutil, "which", lambda _name: None)
    assert isinstance(acquire.innoextract_executable(tmp_path / "tools2"), Failure)
    patch(
        acquire.UrllibClient,
        "get",
        lambda *_args, **_kwargs: Failure("http", "offline"),
    )
    assert isinstance(
        acquire._python_archive(HostSystem.WINDOWS, "x64", tmp_path), Failure
    )
    patch(
        acquire.UrllibClient,
        "get",
        lambda *_args, **_kwargs: HttpResponse(
            200, "https://api.github.com/latest", b"[1]", {}
        ),
    )
    assert isinstance(
        acquire._python_archive(HostSystem.WINDOWS, "x64", tmp_path), Failure
    )
    document = {"assets": [{"name": "other"}]}
    patch(
        acquire.UrllibClient,
        "get",
        lambda *_args, **_kwargs: HttpResponse(
            200, "https://api.github.com/latest", json.dumps(document).encode(), {}
        ),
    )
    assert isinstance(
        acquire._python_archive(HostSystem.WINDOWS, "x64", tmp_path), Failure
    )
    refused = acquire._python_sha256({"assets": []}, "missing.tar.gz", tmp_path)
    assert isinstance(refused, Failure)

    patch(acquire, "download_https", lambda *_args, **_kwargs: Failure("http", "no"))
    sums = {
        "assets": [
            {
                "name": "SHA256SUMS",
                "browser_download_url": "https://example.invalid/SHA256SUMS",
            }
        ]
    }
    assert isinstance(acquire._python_sha256(sums, "cpython.tar.gz", tmp_path), Failure)
    written = tmp_path / "sums"

    def write_sums(
        _url: str, destination: Path, *, minimum_bytes: int, progress: object = None
    ) -> None:
        del minimum_bytes, progress
        destination.write_text("abcd  other\n", encoding="utf-8")
        written.mkdir(exist_ok=True)

    patch(acquire, "download_https", write_sums)
    assert isinstance(acquire._python_sha256(sums, "cpython.tar.gz", tmp_path), Failure)
    assert not acquire._asset_names({"assets": "no"})
    assert acquire._asset_url({"assets": [{"name": "a"}]}, "a") is None
    assert (
        acquire._asset_sha256({"assets": [{"name": "a", "digest": "md5:ab"}]}, "a")
        is None
    )
    assert acquire._asset_url({"assets": "no"}, "a") is None
    assert acquire._asset_sha256({"assets": "no"}, "a") is None
    digest = "a" * 64
    asset_name = "cpython-3.13.0-x86_64-pc-windows-msvc-install_only.tar.gz"
    release = {
        "assets": [
            1,
            {"name": asset_name, "digest": f"sha256:{digest}"},
            {
                "name": asset_name,
                "browser_download_url": "https://example.invalid/python.tar.gz",
                "digest": f"sha256:{digest}",
            },
        ]
    }
    patch(
        acquire.UrllibClient,
        "get",
        lambda *_args, **_kwargs: HttpResponse(
            200, "https://api.github.com/latest", json.dumps(release).encode(), {}
        ),
    )
    patch(acquire, "download_https", lambda *_args, **_kwargs: Failure("http", "no"))
    assert isinstance(
        acquire._python_archive(HostSystem.WINDOWS, "x64", tmp_path / "py"), Failure
    )
    patch(acquire, "download_https", lambda *_args, **_kwargs: None)
    patch(acquire, "check_download", lambda *_args, **_kwargs: Failure("hash", "bad"))
    assert isinstance(
        acquire._python_archive(HostSystem.WINDOWS, "x64", tmp_path / "py"), Failure
    )
    patch(acquire, "check_download", lambda *_args, **_kwargs: None)
    saved = acquire._python_archive(HostSystem.WINDOWS, "x64", tmp_path / "py")
    assert isinstance(saved, Path)
    assert isinstance(
        acquire._python_archive(HostSystem.WINDOWS, "sparc", tmp_path / "py"), Failure
    )
    patch(
        acquire.UrllibClient,
        "get",
        lambda *_args, **_kwargs: HttpResponse(
            200, "https://api.github.com/latest", b"{", {}
        ),
    )
    assert isinstance(
        acquire._python_archive(HostSystem.WINDOWS, "x64", tmp_path / "py"), Failure
    )


def test_install_steps_replace_partial_trees(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    patch = monkeypatch.setattr  # type: ignore[attr-defined]
    real_python = acquire.ensure_python
    real_ide = acquire.ensure_ide
    real_extract = acquire._extract_installer
    real_inno = acquire.innoextract_executable
    choices = InstallChoices(
        ProductKind.IDE,
        InstallScope.USER,
        ProfileMode.FRESH,
        "E:",
        "E:",
    )
    patch(acquire, "copy_tool_package", lambda _path: Failure("copy", "no"))
    assert isinstance(
        acquire.acquire_payload(choices, HostSystem.WINDOWS, tmp_path / "one"),
        Failure,
    )
    patch(acquire, "copy_tool_package", lambda _path: None)
    patch(acquire, "ensure_python", lambda *_args, **_kwargs: Failure("python", "no"))
    assert isinstance(
        acquire.acquire_payload(choices, HostSystem.WINDOWS, tmp_path / "two"),
        Failure,
    )
    patch(acquire, "ensure_python", lambda *_args, **_kwargs: None)
    patch(acquire, "ensure_ide", lambda *_args, **_kwargs: None)
    assert (
        acquire.acquire_payload(choices, HostSystem.WINDOWS, tmp_path / "three") is None
    )
    patch(acquire, "ensure_python", real_python)
    patch(acquire, "ensure_ide", real_ide)
    patch(acquire, "host_arch", lambda: "x64")
    patch(acquire, "_python_archive", lambda *_args, **_kwargs: Failure("python", "no"))
    assert isinstance(
        acquire.ensure_python(HostSystem.WINDOWS, tmp_path / "python"), Failure
    )
    archive = tmp_path / "python.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        payload = BytesIO(b"py")
        info = tarfile.TarInfo("python/python.exe")
        info.size = 2
        handle.addfile(info, payload)
    stale = tmp_path / "python" / "downloads" / "python-src"
    stale.mkdir(parents=True)
    (stale / "old").write_text("old", encoding="utf-8")
    (tmp_path / "python" / "python").mkdir()
    patch(acquire, "_python_archive", lambda *_args, **_kwargs: archive)
    assert acquire.ensure_python(HostSystem.WINDOWS, tmp_path / "python") is None
    patch(acquire, "ide_platform_id", lambda *_args, **_kwargs: Failure("ide", "bad"))
    assert isinstance(
        acquire.ensure_ide(choices, HostSystem.WINDOWS, tmp_path / "ide"), Failure
    )
    resolved = ResolvedDownload(
        "win32-x64-user", "https://downloads.cursor.com/Cursor.exe", "Cursor.exe"
    )
    patch(acquire, "ide_platform_id", lambda *_args, **_kwargs: "win32-x64-user")
    patch(acquire, "resolve_ide", lambda *_args, **_kwargs: resolved)
    patch(acquire, "download_https", lambda *_args, **_kwargs: None)
    extracted = tmp_path / "e"
    extracted.mkdir(parents=True)
    (tmp_path / "ide" / "ide-user").mkdir(parents=True)
    app = tmp_path / "app"
    app.mkdir()
    patch(acquire, "_extract_installer", lambda *_args, **_kwargs: None)
    patch(acquire, "application_directory", lambda _path: app)
    assert acquire.ensure_ide(choices, HostSystem.WINDOWS, tmp_path / "ide") is None
    patch(acquire, "_extract_installer", real_extract)
    patch(acquire, "_seven_zip", lambda: Failure("7zip", "missing"))
    assert isinstance(
        acquire._extract_installer(
            HostSystem.LINUX,
            tmp_path / "Cursor.dmg",
            tmp_path / "out",
            tmp_path / "tools",
        ),
        Failure,
    )
    patch(
        acquire, "innoextract_executable", lambda _tools: Failure("innoextract", "no")
    )
    assert isinstance(
        acquire._extract_installer(
            HostSystem.WINDOWS,
            tmp_path / "Cursor.exe",
            tmp_path / "out",
            tmp_path / "tools",
        ),
        Failure,
    )
    patch(acquire, "innoextract_executable", real_inno)
    tools = tmp_path / "present"
    tools.mkdir()
    (tools / "innoextract.exe").write_bytes(b"exe")
    (tools / "libbz2-1.dll").write_bytes(b"dll")
    patch(acquire.shutil, "which", lambda _name: None)
    assert acquire.innoextract_executable(tools) == str(tools / "innoextract.exe")
    patch(acquire.shutil, "which", lambda _name: "C:/old/innoextract.exe")
    assert acquire.innoextract_executable(tools, allow_path=False) == str(
        tools / "innoextract.exe"
    )
    patch(acquire.shutil, "which", lambda _name: None)
    patch(acquire, "download_https", lambda *_args, **_kwargs: None)
    assert isinstance(acquire.innoextract_executable(tmp_path / "empty-tools"), Failure)
    packed = tmp_path / "packed"

    def write_zip(
        _url: str,
        destination: Path,
        *,
        minimum_bytes: int,
        expected_sha256: str,
    ) -> None:
        del minimum_bytes, expected_sha256
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w") as handle:
            handle.writestr("libbz2-1.dll", b"dll")
            info = zipfile.ZipInfo("innoextract.exe/")
            handle.writestr(info, b"")

    patch(acquire, "download_https", write_zip)
    assert isinstance(acquire.innoextract_executable(packed), Failure)
    patch(
        acquire.UrllibClient,
        "get",
        lambda *_args, **_kwargs: HttpResponse(
            200, "https://cursor.com/install", b"script", {}
        ),
    )
    patch(
        acquire,
        "cli_archive_url",
        lambda *_args, **_kwargs: "https://example.invalid/cli.zip",
    )
    patch(acquire, "download_https", lambda *_args, **_kwargs: Failure("http", "no"))
    assert isinstance(acquire.ensure_cli(HostSystem.WINDOWS, tmp_path / "cli"), Failure)
    (tmp_path / "cli" / "cli").mkdir(parents=True)
    patch(acquire, "download_https", lambda *_args, **_kwargs: None)
    assert isinstance(acquire.ensure_cli(HostSystem.WINDOWS, tmp_path / "cli"), Failure)
    monkeypatch.delenv("CURSOR_PORTABLE_CACHE", raising=False)  # type: ignore[attr-defined]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))  # type: ignore[attr-defined]
    assert payload_cache() == tmp_path / "local" / "cursor-portable" / "payload"
    monkeypatch.delenv("LOCALAPPDATA", raising=False)  # type: ignore[attr-defined]
    assert payload_cache() == Path.home() / ".cache" / "cursor-portable" / "payload"


def test_linux_host_does_not_run_windows_innoextract(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(acquire.shutil, "which", lambda _name: None)  # type: ignore[attr-defined]
    refused = acquire._extract_installer(
        HostSystem.WINDOWS,
        tmp_path / "CursorSetup.exe",
        tmp_path / "out",
        tmp_path / "tools",
        host=HostSystem.LINUX,
    )
    assert isinstance(refused, Failure)
    assert refused.code == "innoextract"


def _matrix_volume(**kwargs: object) -> Volume:
    base: dict[str, object] = {
        "device_id": "E:",
        "mount_point": "E:\\",
        "label": "STICK",
        "filesystem": "NTFS",
        "size_bytes": 100,
        "removable": True,
        "is_system": False,
        "is_boot": False,
        "writable": True,
    }
    base.update(kwargs)
    return Volume(
        device_id=str(base["device_id"]),
        mount_point=str(base["mount_point"]),
        label=str(base["label"]),
        filesystem=str(base["filesystem"]),
        size_bytes=int(str(base["size_bytes"])),
        removable=bool(base["removable"]),
        is_system=bool(base["is_system"]),
        is_boot=bool(base["is_boot"]),
        writable=bool(base["writable"]),
    )


def test_host_target_matrix(tmp_path: Path, monkeypatch: object) -> None:
    tools = frozenset({"mkfs.ext4", "mkfs.ntfs"})
    windows = format_plan(
        _matrix_volume(filesystem="FAT32"),
        "STICK",
        HostSystem.WINDOWS,
        target=HostSystem.WINDOWS,
        busy=False,
    )
    assert not isinstance(windows, Failure)
    assert "/FS:NTFS" in windows.command
    linux_ntfs = format_plan(
        _matrix_volume(device_id="/dev/sdb1", filesystem="vfat"),
        "STICK",
        HostSystem.LINUX,
        target=HostSystem.WINDOWS,
        busy=False,
        available_tools=tools,
    )
    assert not isinstance(linux_ntfs, Failure)
    assert linux_ntfs.command[0] == "mkfs.ntfs"
    for host, target in (
        (HostSystem.WINDOWS, HostSystem.MACOS),
        (HostSystem.WINDOWS, HostSystem.LINUX),
        (HostSystem.MACOS, HostSystem.WINDOWS),
        (HostSystem.MACOS, HostSystem.LINUX),
        (HostSystem.LINUX, HostSystem.MACOS),
    ):
        device = "E:" if host is HostSystem.WINDOWS else "/dev/sdb1"
        refused = format_plan(
            _matrix_volume(device_id=device, filesystem="vfat"),
            "STICK",
            host,
            target=target,
            busy=False,
            available_tools=tools,
        )
        assert isinstance(refused, Failure)
    missing = format_plan(
        _matrix_volume(device_id="/dev/sdb1", filesystem="vfat"),
        "STICK",
        HostSystem.LINUX,
        target=HostSystem.WINDOWS,
        busy=False,
        available_tools=frozenset(),
    )
    assert isinstance(missing, Failure)
    assert missing.code == "missing-tool"
    ready = classify_drive(
        _matrix_volume(device_id="/dev/sdb1", filesystem="ntfs3"),
        HostSystem.LINUX,
        HostSystem.WINDOWS,
        busy=False,
        available_tools=tools,
    )
    assert ready is DriveDisposition.READY
    readonly = classify_drive(
        _matrix_volume(device_id="/dev/sdb1", filesystem="NTFS", writable=False),
        HostSystem.LINUX,
        HostSystem.WINDOWS,
        busy=False,
        available_tools=tools,
    )
    assert isinstance(readonly, Failure)
    assert readonly.code == "read-only"
    mac_ntfs = classify_drive(
        _matrix_volume(device_id="/dev/disk2", filesystem="NTFS", writable=True),
        HostSystem.MACOS,
        HostSystem.WINDOWS,
        busy=False,
    )
    assert isinstance(mac_ntfs, Failure)
    ntfs_repair = repair_plan(
        _matrix_volume(device_id="/dev/sdb1", filesystem="NTFS"),
        "STICK",
        HostSystem.LINUX,
        RepairMode.CHECK,
        busy=False,
        target=HostSystem.WINDOWS,
    )
    assert isinstance(ntfs_repair, Failure)
    assert display_filesystem("nope") == "nope"
    assert probed_filesystem("nope") == "nope"
    assert label_limit("vfat") == 11
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.disk.filesystem.shutil.which",
        lambda name: "/sbin/mkfs.ext4" if name == "mkfs.ext4" else None,
    )
    assert host_can_format(HostSystem.LINUX, "ext4") is True
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.disk.filesystem.shutil.which",
        lambda _name: None,
    )
    assert host_can_format(HostSystem.LINUX, "ext4") is False
    missing_format = classify_drive(
        _matrix_volume(device_id="/dev/sdb1", filesystem="vfat"),
        HostSystem.LINUX,
        HostSystem.LINUX,
        busy=False,
        available_tools=frozenset(),
    )
    assert isinstance(missing_format, Failure)
    assert missing_format.code == "missing-tool"
    app = tmp_path / "app" / "resources" / "app"
    app.mkdir(parents=True)
    (app / "product.json").write_text('{"version": "9"}', encoding="utf-8")
    layout = StickLayout.from_mount(tmp_path / "mount")
    choices = InstallChoices(
        product=ProductKind.IDE,
        scope=InstallScope.USER,
        profile=ProfileMode.FRESH,
        volume_device="/dev/sdb1",
        typed_label="STICK",
        target_os=HostSystem.LINUX,
        target_arch="arm64",
    )
    assert (
        install_tree(
            tmp_path / "app",
            layout,
            choices,
            HostSystem.WINDOWS,
            sources=None,
            cursor_running=False,
        )
        is None
    )
    assert layout.unix_launcher.is_file()
    assert not layout.windows_launcher.exists()
    assert read_stick_identity(layout.version_file) == ("linux", "arm64", "ext4")
    copied = InstallChoices(
        product=ProductKind.IDE,
        scope=InstallScope.USER,
        profile=ProfileMode.COPY,
        volume_device="/dev/sdb1",
        typed_label="STICK",
        target_os=HostSystem.LINUX,
        target_arch="x64",
    )
    assert isinstance(
        install_tree(
            tmp_path / "app",
            StickLayout.from_mount(tmp_path / "other"),
            copied,
            HostSystem.WINDOWS,
            sources=None,
            cursor_running=False,
        ),
        Failure,
    )


class _Api:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self, *_args: object) -> int:
        return self.value


class _Library:
    def __init__(self, value: int) -> None:
        opened = _Api(value)
        setattr(self, "OpenProcess", opened)
        setattr(self, "CloseHandle", _Api(1))
        setattr(self, "SendMessageTimeoutW", opened)


def test_windows_process_and_path_on_any_host(monkeypatch: object) -> None:
    def kill(pid: int, _sig: int) -> None:
        if pid == 5:
            raise OSError(pid)

    monkeypatch.setattr(processes, "is_windows", lambda: False)  # type: ignore[attr-defined]
    monkeypatch.setattr(processes.os, "kill", kill)  # type: ignore[attr-defined]
    assert pid_alive(0) is False
    assert pid_alive(5) is False
    assert pid_alive(6) is True
    monkeypatch.setattr(processes, "is_windows", lambda: True)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        processes.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: _Library(4),
    )
    assert pid_alive(4) is True
    monkeypatch.setattr(  # type: ignore[attr-defined]
        processes.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: _Library(0),
    )
    assert pid_alive(4) is False
    monkeypatch.setattr(path_store, "is_windows", lambda: True)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        path_store.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: _Library(1),
    )
    broadcast_environment()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        path_store.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: _Library(0),
    )
    broadcast_environment()


def test_windows_copy_and_tools_on_any_host(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    source = tmp_path / "src"
    (source / "nested").mkdir(parents=True)
    (source / "keep.txt").write_text("keep", encoding="utf-8")
    (source / "extra.txt").write_text("extra", encoding="utf-8")
    (source / "drop.txt").write_text("drop", encoding="utf-8")
    (source / "nested" / "inner.txt").write_text("inner", encoding="utf-8")
    monkeypatch.setattr(copy_tree, "is_windows", lambda: True)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        copy_tree.shutil,
        "which",
        lambda name: "robocopy" if name == "robocopy" else None,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        copy_tree.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )
    assert (
        copy_tree.copy_tree(
            source,
            tmp_path / "out",
            skip_names=frozenset({"drop.txt"}),
            skip_dirs=frozenset({"nested"}),
            skip_root_files=frozenset({"extra.txt"}),
        )
        == 1
    )
    assert (
        copy_tree._robocopy(  # pylint: disable=protected-access
            source,
            tmp_path / "plain",
            skip_names=frozenset(),
            skip_dirs=frozenset(),
            skip_root_files=frozenset(),
        )
        is True
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        copy_tree.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=8, stderr="failed", stdout=""
        ),
    )
    assert (
        copy_tree._robocopy(  # pylint: disable=protected-access
            source,
            tmp_path / "bad",
            skip_names=frozenset({"drop.txt"}),
            skip_dirs=frozenset(),
            skip_root_files=frozenset(),
        )
        is False
    )

    def _missing(*_args: object, **_kwargs: object) -> object:
        raise OSError("robocopy missing")

    monkeypatch.setattr(copy_tree.subprocess, "run", _missing)  # type: ignore[attr-defined]
    assert (
        copy_tree._robocopy(  # pylint: disable=protected-access
            source,
            tmp_path / "absent",
            skip_names=frozenset(),
            skip_dirs=frozenset({"nested"}),
            skip_root_files=frozenset(),
        )
        is False
    )
    system = tmp_path / "System32"
    system.mkdir()
    (system / "chkdsk.exe").write_bytes(b"")
    monkeypatch.setenv("SystemRoot", str(tmp_path))  # type: ignore[attr-defined]
    resolved = _windows_tool(("chkdsk", "E:"))
    assert resolved[0].endswith("chkdsk.exe")
    assert _windows_tool(("format.com", "E:")) == ("format.com", "E:")
    monkeypatch.delenv("SystemRoot", raising=False)  # type: ignore[attr-defined]
    assert _windows_tool(("chkdsk",)) == ("chkdsk",)
    assert _windows_tool(("diskutil",)) == ("diskutil",)
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cursor_portable.install.acquire.innoextract_executable",
        lambda _tools: Failure("innoextract", "missing"),
    )
    refused = _extract_windows_installer(
        tmp_path / "setup.exe",
        tmp_path / "dest",
        tmp_path / "tools",
        HostSystem.WINDOWS,
    )
    assert isinstance(refused, Failure)
    assert refused.code == "innoextract"


def test_bundled_extractors_are_preferred(tmp_path: Path, monkeypatch: object) -> None:
    root = tmp_path / "setup"
    executable = root / "python" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"py")
    tools = root / "tools"
    tools.mkdir()
    (tools / "7zz.exe").write_bytes(b"7z")
    (tools / "innoextract.exe").write_bytes(b"inn")
    (tools / "libbz2-1.dll").write_bytes(b"dll")
    monkeypatch.setattr(acquire.sys, "executable", str(executable))  # type: ignore[attr-defined]
    assert acquire.bundled_tool(("7zz.exe",)) == tools / "7zz.exe"
    assert acquire.bundled_tool(("missing-tool",)) is None
    assert acquire._seven_zip() == str(tools / "7zz.exe")
    assert acquire.innoextract_executable(tmp_path / "other", allow_path=False) == str(
        tools / "innoextract.exe"
    )
