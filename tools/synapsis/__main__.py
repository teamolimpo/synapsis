"""Entry point: uv run python -m tools.synapsis

Behaviour depends on arguments:
- With subcommand (e.g. ``compress``, ``vacuum``, ``stats``, ``knowledge`` ...): runs CLI mode
- With root options like --help / -h: shows CLI help
- Without arguments: starts the MCP server on stdio transport
"""

from __future__ import annotations

import sys


def main() -> None:
    """Dispatch entry point for `python -m tools.synapsis` and the `synapsis` console script (from pyproject [project.scripts]).

    - Without arguments: starts the MCP server on stdio transport (preserves prior behaviour for MCP clients).
    - With any subcommand (e.g. compress, vacuum, stats, knowledge ..., problem for escalation, or any future @app.command)
      or root option (--help/-h/...): runs the Typer CLI.

    The previous hardcoded cli_commands whitelist has been removed: adding CLI subcommands in cli.py is now trivial and non-fragile.
    The def main() makes the console script entrypoint actually resolve (was previously missing).
    """
    if len(sys.argv) > 1:
        # Any first arg (subcommand or flag like --help) dispatches to CLI.
        # Unknown subcommands will get a clear Typer error instead of silently starting the server.
        from tools.synapsis.cli import app
        app()
    else:
        from tools.synapsis.server import main_server
        main_server()


if __name__ == "__main__":
    main()
