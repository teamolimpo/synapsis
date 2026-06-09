"""Knowledge Base MCP server for Team Olimpo.

Provides grep-based search (``kb_search``) and file read (``kb_read``)
for the configured knowledge base directories (declared in .synapsis/config.yaml under knowledge.include).

Sub-modules
-----------
- ``grep_engine`` — ripgrep/grep execution, output parsing, frontmatter extraction
- ``heading_chunker`` — H2/H3 heading-based Markdown chunking
- ``chunk_indexer`` — SQLite + FTS5 chunk index with CLI (rebuild/update/clean)
- ``vector_indexer`` — SentenceTransformer embedding + sqlite-vec vector index
- ``rrf_fusion`` — Reciprocal Rank Fusion for BM25 + embedding hybrid search
- ``entity_extractor`` — Dictionary + pattern entity extraction (no spaCy)
- ``entity_dictionary.yaml`` — Custom YAML dictionary of Team Olimpo entities
- ``server`` — MCP server tool registration (kb_search, kb_read)
"""
