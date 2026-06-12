"""MCP server DISMISSED.

This MCP server was retired in May 2026. The ``kb_search`` tool
has been incorporated into Synapsis as ``knowledge_search``.

The libraries (``chunk_indexer``, ``entity_extractor``, ``heading_chunker``,
``vector_indexer``, ``rrf_fusion``, ``grep_engine``) are still active and
importable — Synapsis uses them internally.

Remove this file once there are no more external references.
"""

import sys


def main_server() -> None:
    print("knowledge_base MCP server is DISMISSED.", file=sys.stderr)
    print("Use Synapsis knowledge_search instead.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main_server()
