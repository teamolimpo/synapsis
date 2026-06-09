"""Entry point: python -m tools.synapsis

Behaviour depends on arguments:
- With subcommand (e.g. ``compress``, ``vacuum``, ``migrate``): runs CLI mode
- Without arguments: starts the MCP server on stdio transport
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    # Check if we have a subcommand
    cli_commands = {"compress", "vacuum", "migrate", "stats", "cross-ref", "domain", "hygiene"}
    if len(sys.argv) > 1 and sys.argv[1] in cli_commands:
        from tools.synapsis.cli import app

        app()
    else:
        from tools.synapsis.server import main_server

        main_server()
