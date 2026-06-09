"""Entry point — DISMESSO.

Il tool ``kb_search`` è stato incorporato in Synapsis.
Vedi ``tools/synapsis/server.py`` → ``knowledge_search``.
"""

from tools.knowledge_base.server import main_server

if __name__ == "__main__":
    main_server()
