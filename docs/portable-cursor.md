# Portable layout

The wizard downloads the official Cursor build and a relocatable Python on the computer where it runs, then copies that build onto the stick. The stick is for the person who created it.



```text
CursorPortable/
  app/                  Cursor files, with product.json target removed
  data/user-data/       settings and chats
  data/extensions/      extensions
  data/dot-cursor/      skills, rules, MCP config, and agents
  python/               relocatable CPython 3.14 with pip
  tool/                 this package, imported by the launchers
  cli/                  Cursor CLI, when selected
  CursorPortable.cmd    Windows launcher, only on a Windows stick
  cursor-portable       macOS or Linux launcher, only on that system's stick
  meta/version.json
```

The launcher copies the application and the portable profile to an internal cache for that stick and starts Cursor from there. A `data` directory sits beside the cached executable, which is what keeps Cursor in portable mode. `--user-data-dir` and `--extensions-dir` point at that cache. While Cursor is open, `~/.cursor` points at the portable profile and the previous directory is restored on exit. On macOS the portable Python is prepended in `.zprofile`, `.zshrc`, and `.profile`. On Linux it is prepended in `.profile` and `.bashrc`. Windows uses the user PATH. The profile is copied back to the drive every few minutes, skipping chat databases during that copy, and the full profile is copied again when Cursor exits. A crash is recovered on the next launch of the same stick. `--disk-cache-dir` and the process temporary directory stay on the internal disk. The Cursor process is started with `NODE_NO_WARNINGS=1` so its Node runtime does not print the `url.parse()` deprecation (`DEP0169`) into the launcher console. Packaged Electron ignores `NODE_OPTIONS`, so that is the setting that reaches Cursor. The `~/.cursor` link stays on the internal disk.

Double-clicking `CursorPortable.cmd`, or starting the Cursor Portable shortcut, does not leave a command window open. The launcher keeps running with no window until Cursor exits, then copies the profile back and restores PATH and `~/.cursor`. Closing a command window cannot end that session. `CursorPortable.cmd --console`, or `CURSOR_PORTABLE_CONSOLE=1`, keeps the console attached and shows its exit code.

## Python, pip, and uv

The stick's `python/` directory is a relocatable CPython 3.14 build from python-build-standalone. That build includes pip: `python.exe` and `Scripts\pip.exe` on Windows, and `bin/python3`, `bin/pip`, and `bin/pip3` on macOS and Linux. The launcher imports this package from `tool/` through `PYTHONPATH`. uv and the Windows `py` launcher stay the copies already installed on the computer.

While Cursor is open, the stick interpreter is prepended on PATH when that exact directory is not already present. Windows prepends the interpreter directory and `Scripts` on the user PATH. macOS prepends `bin` in `.zprofile`, `.zshrc`, and `.profile`. Linux prepends `bin` in `.profile` and `.bashrc`. The Cursor process receives the same PATH immediately. A new shell then finds the stick's `python` and `pip` first, so `pip install` writes into the stick's site-packages and those packages remain on the drive. Directories that were already on PATH, including an existing pip and uv, stay behind that entry. `uv` remains that existing binary. `py` continues to start the registered CPython. A terminal that is already open keeps the PATH it started with.

When Cursor exits, the directories added for that session are removed. A directory that was already on PATH stays where it was. Uninstall removes the host cache, that PATH entry, and the shortcuts this tool created. The stick is left as it is.

An opt-in Linux container checks this PATH behavior. See [docker-session.md](docker-session.md).

## Settings copy

A fresh install removes the portable user data, extensions, and `.cursor` profile on the drive, then creates empty data directories. A copy reads the allowlisted folders from this computer and skips chat databases while Cursor is open. `state.vscdb.backup` is skipped unless a caller asks for it. Inno Setup uninstallers are not copied.

This tool does not protect sign-in tokens. Windows may bind them to the account that created them, and another computer may have to sign in again.

## Drive format and repair

Each stick uses the filesystem of its target system: NTFS on Windows, APFS on macOS, and ext4 on Linux. The wizard asks for that system and CPU. A profile copied from this computer is offered only when the target system is the same as this computer. A cross-system stick starts empty.

Install continues only when this computer can write that filesystem and the volume is already formatted for it. A removable drive with another filesystem can be formatted when this computer has the tool: `format.com` for NTFS, `diskutil` for APFS, and `mkfs.ext4` or `mkfs.ntfs` when that program is on `PATH`. Windows does not create APFS or ext4. macOS does not create NTFS or ext4. Linux does not create APFS, and it does not repair NTFS. A read-only mount is refused. Formatting asks you to type the current label. A drive with no label is confirmed by typing its drive letter, such as `E:`. `E:\` is accepted as the same confirmation. The system disk and internal disks are refused. A USB, FireWire, SD, or MMC disk counts as portable, including a drive in a USB dock that Windows reports as a fixed SCSI disk with external hard-disk media. On Windows, format and repair ask for approval with a UAC prompt for that command and leave the wizard running as the user who started it. On macOS and Linux the process still has to be root. `--apply` uses the probed volume rather than the caller's removable and system flags. On Windows, repair runs read-only `chkdsk` first and runs `chkdsk /f` only when that output does not say the repair would wait for a reboot. macOS `diskutil repairVolume` and Linux `e2fsck -p` apply directly after the same label is typed.

## In-app update

`update.mode` is left alone. The portable `product.json` has no `target`, and the cached executable has a `data` directory beside it, so Cursor updates that cached application instead of the desktop installer. When Cursor exits, a changed version is copied back to the drive and `target` is removed again. A Cursor setup process started by that update is stopped.

## Shortcuts

Before launch, existing shortcuts are copied, including the per-user Start Menu and the common Start Menu. If one of those shortcuts is rewritten to the portable executable, the original file is put back. The watcher keeps doing this until Cursor exits. This tool's own shortcut is named Cursor Portable.
