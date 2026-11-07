# Docker session

`uv run python scripts/run_docker_session.py` builds a Linux image and runs the portable install inside it. The container has its own Python, a `/usr/local/bin/pip`, and a `/usr/local/bin/uv`. The Windows `py` launcher and the uv environment on the host stay outside that container.

The run needs Docker, a privileged container, and a network connection. The project environment inside the container is Python 3.13, because archive extraction passes `filter` to `tarfile`. The preinstalled pip stays on Debian's Python. The run downloads the relocatable CPython 3.14 archive, formats an ext4 file, and mounts that file as the stick. `install_payload` is given `removable=True` because a loop device is not a USB disk. The production volume probe is unchanged. The official Cursor IDE and CLI archives are not downloaded. A stub `cursor` process holds the session open so a login shell can be checked, then exits.

While the session is open, `python` and `pip` resolve to the stick and `uv` still resolves to `/usr/local/bin/uv`. After exit and uninstall, the login shell matches the paths from before the session, the host pip, uv, and runner interpreter hashes match, and the stick's Python is still on the mount.

This command is not part of `scripts/verify.py` or CI. Windows user PATH remains covered by the fake PATH store in the unit tests.
