# Cursor Portable

Cursor Portable downloads the official Cursor IDE or CLI on this computer and copies it to a removable drive. The drive uses NTFS on Windows, APFS on macOS, and ext4 on Linux. It keeps that system's Cursor build, a relocatable CPython, and one launcher. The Cursor installation already on this computer is not modified.

The setup file does not contain Cursor. The stick is for the person who created it, under that person's own Cursor account.

## Run it

From this repository:

```bash
uv sync --extra dev
uv run python -m cursor_portable
```

Remove the cache, the PATH entry, and the shortcuts this tool created. The removable drive is left as it is. This refuses to run while that Cursor session is still open.

```bash
uv run python -m cursor_portable uninstall
```

A published build is one file from [Releases](https://github.com/tboy1337/cursor-portable/releases):

- `CursorPortableSetup-Windows-x64.exe`
- `CursorPortableSetup-Windows-arm64.exe`
- `CursorPortableSetup-macOS-x64.run`
- `CursorPortableSetup-macOS-arm64.run`
- `CursorPortableSetup-Linux-x64.run`
- `CursorPortableSetup-Linux-arm64.run`

Run that file. On macOS or Linux, `./CursorPortableSetup-….run uninstall` does the same host cleanup. On Windows, use the Uninstall button in the setup window.

## Wizard

The wizard opens on the drive page with this computer already selected: this operating system, this CPU, the user build, the IDE and the CLI, and a copy of this computer's profile. Back changes those choices. One matching removable drive is selected. A drive that needs formatting still requires its label to be typed.

Re-running the setup updates the portable install. An existing portable profile on the drive is kept unless the wizard is set to a fresh profile, which removes the portable user data, extensions, and `.cursor` profile on the drive.

## Launcher on the stick

The wizard writes one launcher onto the stick. On Windows that file is `CursorPortable.cmd`. Starting it closes the command window immediately, and the launcher keeps running, with no window of its own, until Cursor exits. On macOS or Linux the launcher is `cursor-portable`. It copies the application to a local cache for that drive, prepends the portable Python to your user PATH, points `~/.cursor` at the portable profile for the session, and puts both back when Cursor exits.

Details are in [docs/portable-cursor.md](docs/portable-cursor.md) and [docs/Architecture.md](docs/Architecture.md).
