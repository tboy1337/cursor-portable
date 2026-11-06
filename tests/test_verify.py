"""Quality gate command selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify import _shellcheck


def test_shellcheck_stays_beside_the_venv_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "interpreter" / "bin" / "python"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"")
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    link = venv_bin / "python"
    tool = venv_bin / "shellcheck"
    tool.write_bytes(b"")
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks are unavailable")
    monkeypatch.setattr("scripts.verify.sys.executable", str(link))
    monkeypatch.setattr("scripts.verify.sys.platform", "linux")
    assert Path(_shellcheck()) == tool
