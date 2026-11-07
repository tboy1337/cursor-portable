# Architecture

The tkinter wizard collects choices and calls into modules that do not import tkinter.

- `download` resolves official HTTPS URLs, refuses a non-HTTPS redirect, and checks size. SHA-256 is checked when a digest is published. The CLI install script is read as text and is not executed. Cursor itself is downloaded when a person runs the wizard.
- `repack` strips `product.json` `target` on the copy that person downloaded, writes launchers, and builds a 7-Zip self-extractor that contains this tool and Python. macOS and Linux get a bash stub with a bundled extractor because 7-Zip has no self-extractor for those systems.
- `disk` refuses system and internal volumes before it builds a format or repair command. A USB dock counts when Windows reports a fixed SCSI disk on the USB bus or as external media. A Windows stick uses NTFS, a macOS stick uses APFS, and a Linux stick uses ext4. Format and repair use the host's own tools, and a host that cannot write the target filesystem is refused.
- `profile` copies an allowlist and will not rewrite `settings.json` when it is not plain JSON.
- `launcher` keeps the session profile on the internal disk, beside the cached executable, and copies it back to the stick on a timer, on exit, and after a crash. A session file lets the next start drop the PATH entry and restore `~/.cursor`. On Windows that process has no console window, so closing a command window cannot end the session.
- `host` deletes only the local cache, the PATH segment, and shortcuts this tool created. It also restores `~/.cursor` when the session link belongs to this tool.
