"""Entry point — DISMISSED.

The ``kb_search`` tool has been incorporated into Synapsis.
See ``tools/synapsis/server.py`` → ``knowledge_search``.
"""

from tools.knowledge_base.server import main_server

if __name__ == "__main__":
    main_server()
