"""Tests for chunk_indexer.py — database operations and CLI commands.

These tests use an in-memory SQLite database to avoid side effects.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tools.knowledge_base.chunk_indexer import (
    SCHEMA_SQL,
    TRIGGERS_SQL,
    _chunk_id,
    _file_hash,
    app,
)

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db() -> sqlite3.Connection:
    """Create an in-memory database with the chunks schema."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=OFF;")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    try:
        conn.executescript(TRIGGERS_SQL)
    except sqlite3.OperationalError:
        pass
    return conn


def _insert_chunk(
    conn: sqlite3.Connection,
    file_path: str = "/test.md",
    heading_path: str = "/Section",
    content: str = "Some content here.",
    file_hash: str = "aaaa",
    heading_level: int = 2,
    frontmatter: str = '{"title": "Test"}',
    line_start: int = 1,
    line_end: int = 5,
) -> str:
    cid = _chunk_id(file_path, heading_path, content)
    conn.execute(
        """INSERT OR REPLACE INTO chunks
        (id, file_path, file_hash, heading_path, heading_level,
         content, frontmatter, token_count, line_start, line_end)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            cid,
            file_path,
            file_hash,
            heading_path,
            heading_level,
            content,
            frontmatter,
            len(content) // 4,
            line_start,
            line_end,
        ),
    )
    conn.commit()
    return cid


# ---------------------------------------------------------------------------
# _chunk_id
# ---------------------------------------------------------------------------


class TestChunkId:
    def test_deterministic(self) -> None:
        a = _chunk_id("/a.md", "/b", "content")
        b = _chunk_id("/a.md", "/b", "content")
        assert a == b

    def test_different_content_different_id(self) -> None:
        a = _chunk_id("/a.md", "/b", "content1")
        b = _chunk_id("/a.md", "/b", "content2")
        assert a != b

    def test_length(self) -> None:
        cid = _chunk_id("/x.md", "/y", "z")
        assert len(cid) == 16


# ---------------------------------------------------------------------------
# _file_hash
# ---------------------------------------------------------------------------


class TestFileHash:
    def test_known_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("hello world")
            tmp = f.name
        try:
            h = _file_hash(Path(tmp))
            expected = hashlib.sha256(b"hello world").hexdigest()
            assert h == expected
        finally:
            Path(tmp).unlink()

    def test_missing_file(self) -> None:
        h = _file_hash(Path("/nonexistent/file.md"))
        assert h == ""


# ---------------------------------------------------------------------------
# Schema and triggers
# ---------------------------------------------------------------------------


class TestSchema:
    def test_tables_created(self) -> None:
        conn = _make_db()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [r["name"] for r in tables]
        assert "chunks" in names
        assert "chunks_fts" in names
        assert "file_state" in names
        conn.close()

    def test_triggers_created(self) -> None:
        conn = _make_db()
        triggers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
        ).fetchall()
        names = [r["name"] for r in triggers]
        assert "chunks_ai" in names
        assert "chunks_ad" in names
        assert "chunks_au" in names
        conn.close()

    def test_fts_auto_sync_on_insert(self) -> None:
        """Inserting into chunks should auto-populate chunks_fts."""
        conn = _make_db()
        _insert_chunk(conn)
        # Verify in FTS
        row = conn.execute("SELECT rowid FROM chunks_fts WHERE content MATCH 'content'").fetchone()
        assert row is not None
        conn.close()

    def test_fts_auto_sync_on_delete(self) -> None:
        """Deleting from chunks should remove from chunks_fts."""
        conn = _make_db()
        cid = _insert_chunk(conn)
        conn.execute("DELETE FROM chunks WHERE id = ?", (cid,))
        conn.commit()
        row = conn.execute("SELECT count(*) as cnt FROM chunks_fts").fetchone()
        assert row["cnt"] == 0
        conn.close()


# ---------------------------------------------------------------------------
# search_chunks helper
# ---------------------------------------------------------------------------


class TestSearchChunks:
    def test_search_no_index(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When DB file doesn't exist, return empty list."""
        # Point to nonexistent path
        monkeypatch.setattr(
            "tools.knowledge_base.chunk_indexer.DB_PATH",
            Path("/nonexistent/chunks.db"),
        )
        from tools.knowledge_base.chunk_indexer import search_chunks

        results = search_chunks("test")
        assert results == []

    def test_search_basic(self) -> None:
        conn = _make_db()
        _insert_chunk(conn, content="MCP server design patterns")
        _insert_chunk(conn, content="Something about deployment")
        conn.close()

        # We can't easily test the full search_chunks due to DB_PATH,
        # but we can test the FTS query directly in the integration test.
        pass


# ---------------------------------------------------------------------------
# CLI — smoke tests
# ---------------------------------------------------------------------------


class TestCli:
    def test_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "chunk_indexer" in result.stdout

    def test_rebuild_help(self) -> None:
        result = runner.invoke(app, ["rebuild", "--help"])
        assert result.exit_code == 0

    def test_update_help(self) -> None:
        result = runner.invoke(app, ["update", "--help"])
        assert result.exit_code == 0

    def test_clean_help(self) -> None:
        result = runner.invoke(app, ["clean", "--help"])
        assert result.exit_code == 0

    def test_update_dry_run(self) -> None:
        """update --dry-run should succeed without side effects."""
        result = runner.invoke(app, ["update", "--dry-run"])
        assert result.exit_code == 0

    def test_clean_dry_run(self) -> None:
        """clean --dry-run should succeed without side effects."""
        result = runner.invoke(app, ["clean", "--dry-run"])
        assert result.exit_code == 0

    def test_clean_vectors_help(self) -> None:
        """clean-vectors --help should show usage."""
        result = runner.invoke(app, ["clean-vectors", "--help"])
        assert result.exit_code == 0
        assert "orphan vectors" in result.stdout or "chunk_id" in result.stdout

    def test_clean_vectors_dry_run(self) -> None:
        """clean-vectors --dry-run should succeed without side effects."""
        result = runner.invoke(app, ["clean-vectors", "--dry-run"])
        assert result.exit_code == 0
