"""python -m paypilot ... (CLI in paypilot/cli/app.py)."""
from paypilot.cli.app import app
from paypilot.cli import extra as _extra  # noqa: F401  (registers exceptions / audit / demo / fake-provider commands)


def _configure_console_encoding() -> None:
    """Keep CLI output (notably INR symbols) usable in legacy Windows consoles."""
    import sys
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                # Use UTF-8 for an interactive terminal. For redirected pipes, retain
                # the consumer's expected encoding and replace unsupported symbols.
                reconfigure(encoding="utf-8" if stream.isatty() else stream.encoding, errors="replace")
            except (OSError, ValueError):  # e.g. a closed or embedded capture stream
                pass


def main() -> int:
    _configure_console_encoding()
    app()          # typer exits the process with the command's exit code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
