"""Entry point: uv run python -m tools.synapsis

Behaviour depends on arguments:
- With subcommand (e.g. ``compress``, ``vacuum``, ``stats``, ``knowledge`` ...): runs CLI mode
- With root options like --help / -h: shows CLI help
- Without arguments: starts the MCP server on stdio transport
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    # Check if we have a subcommand or root options (like --help).
    # This allows `synapsis --help` (and -h) to show typer help instead of
    # starting the MCP server.
    cli_commands = {"compress", "vacuum", "stats", "cross-ref", "domain", "hygiene", "knowledge"}
    if len(sys.argv) > 1:
        arg1 = sys.argv[1]
        if arg1 in cli_commands or arg1.startswith("-"):
            from tools.synapsis.cli import app
            app()
        else:
            from tools.synapsis.server import main_server
            main_server()
    else:
        from tools.synapsis.server import main_server
        main_server()
