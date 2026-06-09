"""Integration tests: chunk → index → search for the sample document.

These tests exercise the full pipeline:
1. ``chunk_markdown`` produces correct chunks.
2. Chunks are inserted into a real (in-memory) SQLite + FTS5 database.
3. FTS5 search returns matching chunks.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tools.knowledge_base.chunk_indexer import (
    SCHEMA_SQL,
    TRIGGERS_SQL,
    _chunk_id,
)
from tools.knowledge_base.heading_chunker import chunk_markdown

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_MD = FIXTURES / "sample.md"


@pytest.fixture
def sample_text() -> str:
    return SAMPLE_MD.read_text(encoding="utf-8")


@pytest.fixture
def in_memory_db() -> sqlite3.Connection:
    """Create in-memory SQLite with full schema + FTS5."""
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


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    def test_chunk_and_index(self, sample_text: str, in_memory_db: sqlite3.Connection) -> None:
        """Chunk the sample file, insert into DB, verify via SQL."""
        chunks = chunk_markdown(sample_text, str(SAMPLE_MD))
        # With min_chunk_size=50, 57-line file produces 5 chunks after merge
        assert len(chunks) == 5

        cur = in_memory_db.cursor()
        for chunk in chunks:
            cid = _chunk_id(chunk.file_path, chunk.heading_path, chunk.content)
            fm_json = json.dumps(chunk.frontmatter, ensure_ascii=False)
            cur.execute(
                """INSERT OR REPLACE INTO chunks
                (id, file_path, file_hash, heading_path, heading_level,
                 content, frontmatter, token_count, line_start, line_end)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cid,
                    chunk.file_path,
                    chunk.file_hash,
                    chunk.heading_path,
                    chunk.heading_level,
                    chunk.content,
                    fm_json,
                    chunk.token_count,
                    chunk.start_line,
                    chunk.end_line,
                ),
            )
        in_memory_db.commit()

        # Verify count in DB
        row = cur.execute("SELECT count(*) as cnt FROM chunks").fetchone()
        assert row["cnt"] == 5

    def test_fts_query(self, sample_text: str, in_memory_db: sqlite3.Connection) -> None:
        """Index chunks and run FTS5 queries."""
        chunks = chunk_markdown(sample_text, str(SAMPLE_MD))
        cur = in_memory_db.cursor()
        for chunk in chunks:
            cid = _chunk_id(chunk.file_path, chunk.heading_path, chunk.content)
            fm_json = json.dumps(chunk.frontmatter, ensure_ascii=False)
            cur.execute(
                """INSERT INTO chunks
                (id, file_path, file_hash, heading_path, heading_level,
                 content, frontmatter, token_count, line_start, line_end)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cid,
                    chunk.file_path,
                    chunk.file_hash,
                    chunk.heading_path,
                    chunk.heading_level,
                    chunk.content,
                    fm_json,
                    chunk.token_count,
                    chunk.start_line,
                    chunk.end_line,
                ),
            )
        in_memory_db.commit()

        # Search for "Architecture"
        rows = cur.execute(
            """SELECT c.heading_path, c.content
               FROM chunks_fts
               JOIN chunks c ON c.rowid = chunks_fts.rowid
               WHERE chunks_fts MATCH ?
               ORDER BY rank""",
            ("Architecture",),
        ).fetchall()
        assert len(rows) > 0
        found_paths = {r["heading_path"] for r in rows}
        assert "/Architecture" in found_paths

        # Search for "preamble" (was merged into /Architecture preamble text)
        rows = cur.execute(
            """SELECT c.heading_path, c.content
               FROM chunks_fts
               JOIN chunks c ON c.rowid = chunks_fts.rowid
               WHERE chunks_fts MATCH ?
               ORDER BY rank""",
            ("preamble",),
        ).fetchall()
        assert len(rows) > 0

        # Search for something in code block — "print" should be searchable
        rows = cur.execute(
            """SELECT c.heading_path, c.content
               FROM chunks_fts
               JOIN chunks c ON c.rowid = chunks_fts.rowid
               WHERE chunks_fts MATCH ?
               ORDER BY rank""",
            ("print",),
        ).fetchall()
        assert len(rows) > 0

    def test_heading_in_code_not_split(self, sample_text: str) -> None:
        """Verify the code block heading is NOT a split point."""
        chunks = chunk_markdown(sample_text, str(SAMPLE_MD))
        for c in chunks:
            assert "heading is inside" not in c.heading_path

    def test_preamble_content(self, sample_text: str) -> None:
        """Preamble text should be present (even if merged into first section)."""
        chunks = chunk_markdown(sample_text, str(SAMPLE_MD))
        # Preamble was merged into /Architecture (first chunk)
        assert "Introduction" in chunks[0].content

    def test_heading_path_hierarchy(self, sample_text: str) -> None:
        """Verify heading paths after merge (non-cascading)."""
        chunks = chunk_markdown(sample_text, str(SAMPLE_MD))
        paths = {c.heading_path for c in chunks}
        # These paths exist after non-cascading merge:
        assert "/Architecture" in paths
        assert "/Architecture/Components" in paths  # Patterns merged into Components
        assert "/Deployment/Docker" in paths
        assert "/Final Section" in paths  # Kubernetes merged into Final
        assert "/Final Section/Another Sub" in paths
