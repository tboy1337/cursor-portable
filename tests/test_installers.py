"""Installer and publish script checks."""

from __future__ import annotations

from pathlib import Path
import subprocess

from scripts.ci_publish import main as publish_main

_COMMIT = "a" * 40


def test_publish_combines_checksums(tmp_path: Path, monkeypatch: object) -> None:
    packs = tmp_path / "packs"
    packs.mkdir()
    (packs / "b.exe.sha256").write_text("bbb  b.exe\n", encoding="utf-8")
    (packs / "a.exe.sha256").write_text("aaa  a.exe\n", encoding="utf-8")
    (packs / "a.exe").write_bytes(b"exe")
    seen: dict[str, list[str]] = {}

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "scripts.ci_publish.subprocess.run", fake_run
    )
    code = publish_main(
        [
            "--packs",
            str(packs),
            "--version",
            "1.2.3",
            "--target",
            _COMMIT,
            "--sums",
            str(tmp_path / "SHA256SUMS"),
            "--notes",
            str(tmp_path / "notes.md"),
        ]
    )
    assert code == 0
    assert (tmp_path / "SHA256SUMS").read_text(encoding="utf-8") == (
        "aaa  a.exe\nbbb  b.exe\n"
    )
    notes = (tmp_path / "notes.md").read_text(encoding="utf-8")
    assert "Cursor Portable 1.2.3." in notes
    assert "does not contain Cursor" in notes
    command = seen["command"]
    assert command[:4] == ["gh", "release", "create", "1.2.3"]
    assert str(tmp_path / "SHA256SUMS") in command
    assert "--target" in command
    assert _COMMIT in command


def test_publish_refuses_a_bad_version(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    packs.mkdir()
    assert (
        publish_main(
            [
                "--packs",
                str(packs),
                "--version",
                "1.2",
                "--target",
                _COMMIT,
            ]
        )
        == 1
    )
