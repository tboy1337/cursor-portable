# Agent guide

Run `uv run python scripts/verify.py` before handing work back. The gate covers autopep8, isort, pyproject-fmt, black, yamllint, mypy, pylint, bandit, complexipy, pip-audit, blinter, shellcheck, and pytest. On Windows it also runs PSScriptAnalyzer and Pester against the generated launcher scripts. Use the project environment: a system Python does not have this package installed. The wizard uses tkinter from that environment.

Library code logs through `logging`. Expected refusals return `Failure` instead of raising. Do not modify an installed Cursor. Disk format and repair stay behind `guard_volume` and a typed label.

The wizard downloads the official Cursor build and a relocatable Python on the computer where it runs. `cursor-portable uninstall` removes the host cache, PATH entry, and shortcuts, and it does not change the removable drive.

Tests use temporary directories and fake PATH stores. They do not download Cursor and they do not format disks.
