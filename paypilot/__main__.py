"""python -m paypilot ... (CLI in paypilot/cli/app.py)."""
from paypilot.cli.app import app


def main() -> int:
    app()          # typer exits the process with the command's exit code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
