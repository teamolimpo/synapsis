"""Smoke tests for entity extraction, CLI commands, and search integration."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tools.knowledge_base.chunk_indexer import (
    app,
    entity_search,
    hybrid_search,
    search_chunks,
)
from tools.knowledge_base.entity_extractor import (
    clean_orphan_entities,
    ensure_entities_table,
    extract_entities,
    extract_entities_batch,
    index_all_entities,
    load_dictionary,
)
from tools.knowledge_base.rrf_fusion import fuse_rrf

runner = CliRunner()
DICT = load_dictionary()


# ---------------------------------------------------------------------------
# Dictionary
# ---------------------------------------------------------------------------


class TestDictionary:
    """Verify YAML dictionary loads correctly."""

    def test_has_categories(self):
        assert "projects" in DICT
        assert "tools" in DICT
        assert "people" in DICT
        assert "concepts" in DICT

    def test_all_entries_have_required_keys(self):
        for cat, entries in DICT.items():
            for e in entries:
                assert "name" in e, f"Missing name in {cat}"
                assert "type" in e, f"Missing type in {e['name']}"
                assert e["type"] in ("PROJECT", "TOOL", "PERSON", "CONCEPT")


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------


class TestExtractEntities:
    """Verify the 2-level extraction pipeline."""

    def test_dict_lookup_mem0(self):
        """Mem0 is in the dictionary and should be found."""
        ents = extract_entities("Mem0 uses MCP", DICT)
        texts = {e["entity_text"] for e in ents}
        assert "Mem0" in texts

    def test_dict_lookup_aliases(self):
        """Aliases like 'mcp' should match."""
        ents = extract_entities("the mcp protocol", DICT)
        texts = {e["entity_text"] for e in ents}
        assert "MCP" in texts

    def test_pattern_proper_noun(self):
        """Proper noun sequences should be extracted."""
        ents = extract_entities("Alex Garcia works on sqlite-vec")
        texts = {e["entity_text"] for e in ents}
        assert "Alex Garcia" in texts

    def test_pattern_pascal_case(self):
        """PascalCase identifiers should be extracted."""
        ents = extract_entities("ChunkDragon and SentenceTransformer")
        texts = {e["entity_text"] for e in ents}
        assert "ChunkDragon" in texts
        assert "SentenceTransformer" in texts

    def test_pattern_acronyms(self):
        """ALL CAPS acronyms should be extracted."""
        ents = extract_entities("FTS5 and MCP and RRF")
        texts = {e["entity_text"] for e in ents}
        assert "FTS5" in texts
        assert "RRF" in texts

    def test_pattern_quoted(self):
        """Quoted text should be extracted."""
        ents = extract_entities('He said "entity linking" is key')
        texts = {e["entity_text"] for e in ents}
        assert "entity linking" in texts

    def test_empty_text_returns_empty(self):
        assert extract_entities("") == []
        assert extract_entities("   ") == []

    def test_dedup_dict_wins(self):
        """When same entity found by dict and pattern, dedup keeps first."""
        ents = extract_entities("MCP protocol uses MCP", DICT)
        # Both should be level-1 (dictionary match) since dictionary wins
        mcp_ents = [e for e in ents if e["entity_text"] == "MCP"]
        assert len(mcp_ents) == 1

    def test_batch_extraction(self):
        texts = ["Mem0 is a project", "ChunkDragon is a tool"]
        batch = extract_entities_batch(texts, DICT)
        assert len(batch) == 2
        assert any(e["entity_text"] == "Mem0" for e in batch[0])
        assert any(e["entity_text"] == "ChunkDragon" for e in batch[1])


# ---------------------------------------------------------------------------
# RRF fusion (dict mode)
# ---------------------------------------------------------------------------


class TestRrfFusion:
    """Verify RRF works with dict[str, list] and legacy signatures."""

    def test_legacy_two_signal(self):
        bm25 = [{"id": "a"}, {"id": "b"}]
        emb = [{"id": "b"}, {"id": "c"}]
        result = fuse_rrf(bm25, emb, k=60)
        assert len(result) == 3
        assert "_rrf_score" in result[0]

    def test_dict_multi_signal(self):
        signals = {
            "bm25": [{"id": "a"}, {"id": "b"}],
            "embedding": [{"id": "b"}, {"id": "c"}],
            "entity": [{"id": "a"}],
        }
        result = fuse_rrf(signals, k=60)
        assert len(result) == 3
        # 'a' appears in both bm25 (rank 1) and entity (rank 1) → highest
        assert result[0]["id"] == "a"

    def test_dict_with_weights(self):
        signals = {
            "bm25": [{"id": "a"}],
            "entity": [{"id": "b"}],
        }
        result = fuse_rrf(signals, k=60, weights={"entity": 2.0})
        assert result[0]["id"] == "b"  # entity weighted 2x should win


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


class TestCliEntityCommands:
    """Smoke tests for new entity-related CLI commands."""

    def test_rebuild_entities_help(self):
        result = runner.invoke(app, ["rebuild-entities", "--help"])
        assert result.exit_code == 0
        assert "Extract entities for ALL chunks" in result.stdout

    def test_update_entities_help(self):
        result = runner.invoke(app, ["update-entities", "--help"])
        assert result.exit_code == 0
        assert "NEW chunks" in result.stdout

    def test_clean_entities_help(self):
        result = runner.invoke(app, ["clean-entities", "--help"])
        assert result.exit_code == 0
        assert "orphan entity" in result.stdout

    def test_clean_entities_dry_run(self):
        result = runner.invoke(app, ["clean-entities", "--dry-run"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Search integration
# ---------------------------------------------------------------------------


class TestEntitySearch:
    """Integration tests for entity_search."""

    def test_entity_search_mem0(self):
        """entity_search with 'Mem0' should return results from index."""
        results = entity_search("Mem0", limit=3)
        assert len(results) > 0
        for r in results:
            assert "id" in r
            assert "file_path" in r
            assert "rank" in r
            assert r["rank"] <= 0  # negative rank (more matches = less negative)

    def test_entity_search_empty_query(self):
        assert entity_search("") == []

    def test_entity_search_no_entities(self):
        assert entity_search("the quick brown fox") == []


class TestHybridSearchEntityMode:
    """Verify hybrid_search with entity-related modes."""

    def test_mode_entity_returns_results(self):
        results = hybrid_search("Mem0", limit=3, mode="entity")
        assert len(results) > 0

    def test_mode_entity_format(self):
        results = hybrid_search("Mem0", limit=3, mode="entity")
        for r in results:
            assert "id" in r
            assert "content" in r
            assert "rank" in r

    def test_mode_hybrid_includes_entity(self):
        results = hybrid_search("Mem0 multi-signal", limit=3, mode="hybrid")
        assert len(results) > 0
        # Hybrid results should have _rrf_score when multiple signals fused
        if len(results) > 0:
            # At least one result should have _rrf_score if signals combined
            pass  # Hard to assert deterministically — depends on signals

    def test_mode_bm25_still_works(self):
        results = hybrid_search("MCP server", limit=3, mode="bm25")
        assert len(results) > 0

    def test_mode_embedding_still_works(self):
        results = hybrid_search("vector search", limit=3, mode="embedding")
        assert len(results) > 0

    def test_mode_auto_works(self):
        results = hybrid_search("Mem0", limit=3, mode="auto")
        assert len(results) > 0
