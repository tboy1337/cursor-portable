"""``python -m cursor_portable.wizard`` opens the Qt wizard."""

from cursor_portable.cli.live import payload_directory, wizard_callbacks
from cursor_portable.logging_config import configure_cli_logging
from cursor_portable.models import host_system_name
from cursor_portable.wizard.window import run_wizard


def main() -> int:
    """Open the wizard and probe volumes when the drive page is shown."""
    configure_cli_logging()
    system = host_system_name()
    callbacks = wizard_callbacks(
        payload=payload_directory(),
        system=system,
        probe=True,
    )
    return run_wizard(callbacks)


if __name__ == "__main__":
    raise SystemExit(main())
