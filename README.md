# Cursor Portable

Cursor Portable downloads the official Cursor IDE, the CLI, or both on this computer and copies that build onto a removable drive. The Cursor installation already on this computer is not modified.

This repository does not contain Cursor. The wizard downloads a relocatable CPython 3.13 with pip onto the drive and copies this tool there. The drive is for the person who created it, under that person's own Cursor account. This tool does not protect sign-in tokens. Windows may bind them to the account that created them, and another computer may have to sign in again.

The drive uses NTFS on Windows, APFS on macOS, and ext4 on Linux. It keeps that system's Cursor build, the relocatable CPython, and one launcher.

## From this repository

The package needs Python 3.11 through 3.15. `uv sync` installs it and PySide6.

```bash
uv sync
uv run python -m cursor_portable
```

The dev extra and `uv run python scripts/verify.py` are described in [CONTRIBUTING.md](CONTRIBUTING.md).

## Wizard

The wizard opens on the drive page. The Back button changes the choices already filled in:

- IDE and CLI
- this operating system and this CPU
- the Windows user build, on a Windows target
- a copy of this computer's profile, when the target system is this computer

A different target system starts with an empty profile. When exactly one removable drive can be used, the wizard selects it. A drive that needs formatting still requires its current label to be typed. Typing `E:` or `E:\` confirms a drive with no label. Formatting erases the drive. A read-only mount, the system disk, and non-removable disks are refused.

Re-running the setup updates the portable install. An existing portable profile on the drive is kept unless the wizard is set to a fresh profile. A fresh profile removes the portable user data, extensions, and `.cursor` profile on the drive.

## Launcher

The wizard writes one launcher into `CursorPortable/` on the drive. On Windows that file is `CursorPortable.cmd`. Starting it closes the command window immediately, and the launcher keeps running, with no window of its own, until Cursor exits. `CursorPortable.cmd --console`, or `CURSOR_PORTABLE_CONSOLE=1`, keeps the console attached and shows its exit code. On macOS or Linux the launcher is `cursor-portable`.

The launcher copies the application to a local cache for that drive and points `~/.cursor` at the portable profile for the session. The portable Python is prepended on the Windows user PATH. On macOS it is prepended in `~/.zprofile`, `~/.zshrc`, and `~/.profile`. On Linux it is prepended in `~/.profile` and `~/.bashrc`. PATH and `~/.cursor` are restored when Cursor exits.

## Host cleanup

Uninstall removes the cache, the PATH entry, and the shortcuts this tool created. The removable drive is left unchanged. Uninstall refuses to run while that Cursor session is still open.

Use the Uninstall button in the setup window, or run `uv run python -m cursor_portable uninstall`.

## Layout

The drive layout, profile copy, and in-app update are in [docs/portable-cursor.md](docs/portable-cursor.md). Module boundaries are in [docs/Architecture.md](docs/Architecture.md).

## License

[MIT](LICENSE).
