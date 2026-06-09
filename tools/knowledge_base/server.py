"""MCP server DISMESSO.

Questo MCP server è stato dismesso a maggio 2026. Il tool ``kb_search``
è stato incorporato in Synapsis come ``knowledge_search``.

Le librerie (``chunk_indexer``, ``entity_extractor``, ``heading_chunker``,
``vector_indexer``, ``rrf_fusion``, ``grep_engine``) sono ancora attive e
importabili — Synapsis le usa internamente.

Rimuovere questo file quando non ci sono più riferimenti esterni.
"""

import sys


def main_server() -> None:
    print("knowledge_base MCP server is DISMISSED.", file=sys.stderr)
    print("Use Synapsis knowledge_search instead.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main_server()
