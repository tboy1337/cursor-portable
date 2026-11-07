# Cursor Portable

Cursor Portable downloads the official Cursor IDE, the CLI, or both on your computer and copies that build onto a removable drive.

This repository does not contain Cursor. The wizard downloads a relocatable CPython 3.14 with pip onto the drive and copies this tool there. The drive is for the person who created it, under that person's own Cursor account. This tool does not protect sign-in tokens. Windows may bind them to the account that created them, and another computer may have to sign in again.

The drive uses NTFS on Windows, APFS on macOS, and ext4 on Linux. It keeps that system's Cursor build, the relocatable CPython, and one launcher.

## **Install** (Python 3.11-3.15)
```
pip install cursor-portable
python -m cursor_portable
```
Prefer uv? `uv pip install cursor-portable`, then `python -m cursor_portable`.

## **Launching**
One launcher lives on the drive (`CursorPortable.cmd` on Windows, `cursor-portable` on macOS/Linux). It runs Cursor from a local cache, points `~/.cursor` at the portable profile for the session, and restores your PATH and `~/.cursor` when Cursor exits.

## Host cleanup

Uninstall removes the cache, the PATH entry, and the shortcuts this tool created. The removable drive is left unchanged. Uninstall refuses to run while that Cursor session is still open.

Use the Uninstall button in the setup window. After `uv sync`, run `uv run python -m cursor_portable uninstall`. After `python -m pip install .`, run `python -m cursor_portable uninstall`.

## **Safety bits**
• The drive picker refuses the system disk, internal disks, and read-only mounts. A USB dock counts, including a fixed SCSI disk Windows calls external media
• Formatting makes you type the drive's label first
• `python -m cursor_portable uninstall` removes the host cache, PATH entry and shortcuts and leaves the drive alone

## **Heads up:** the repo doesn't contain Cursor itself (the wizard downloads the official build), and sign-in tokens aren't protected, so use the drive under your own account.

## Layout

The drive layout, profile copy, and in-app update are in [docs/portable-cursor.md](docs/portable-cursor.md). Module boundaries are in [docs/Architecture.md](docs/Architecture.md).

## License

[MIT](LICENSE)
