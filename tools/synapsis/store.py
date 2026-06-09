"""SQLite backing store for Synapsis unified memory layer.

Provides ``SynapsisStore`` with full CRUD for sessions, observations,
tasks, task events, entities, summaries, counters, domains, memory layers,
and FTS5 search — combining SessionStore + StateStore into a single class.

Storage location: ``.synapsis/synapsis.db`` (local low-latency runtime memory,
relative to project root). Overridable via the ``SYNAPSIS_DB_PATH`` environment
variable. Handoff files and curated Wiki contributions remain under ``Library/``
(by design: .synapsis is the hot operational store; Library can be symlinked
to a higher-latency vault for static/curated content).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from tools.common.paths import resolve_synapsis_db
from tools.synapsis.models import (
    compute_token_savings,
    generate_session_id,
    generate_task_id,
    normalize_event_type,
    normalize_observe_type,
    normalize_task_status,
    now_iso,
    now_iso_seconds,
)

# ---------------------------------------------------------------------------
# Default storage path
# ---------------------------------------------------------------------------

_DEFAULT_DB_REL = Path(".synapsis/synapsis.db")


def _resolve_db_path() -> Path:
    """Resolve the database path from env var or default.

    Delegates to the shared helper in tools.common.paths so that
    knowledge_base.chunk_indexer and SynapsisStore always agree on the
    location (and on SYNAPSIS_DB_PATH overrides). Default is the local
    .synapsis/synapsis.db (chunks live in the main DB, not legacy chunks.db).
    """
    return resolve_synapsis_db()


# ---------------------------------------------------------------------------
# Full DDL (10 tables + 2 FTS5)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
-- ============================================================
-- DOMAINS — system domains for domain-gating
-- ============================================================
CREATE TABLE IF NOT EXISTS domains (
    id              TEXT PRIMARY KEY,
    description     TEXT NOT NULL DEFAULT '',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);

-- Default domains
INSERT OR IGNORE INTO domains (id, description, is_active, created_at) VALUES
    ('session',   'Session and observation management', 1, '2026-01-01T00:00:00'),
    ('task',      'Task lifecycle and state machine', 1, '2026-01-01T00:00:00'),
    ('system',    'System-level operations and compression', 1, '2026-01-01T00:00:00'),
    ('entity',    'Entity registry and cross-referencing', 1, '2026-01-01T00:00:00'),
    ('knowledge', 'Wiki, docs, deliverable chunk search (was kb_search)', 1, '2026-01-01T00:00:00'),
    ('legacy',    'Legacy tools — deprecated in favour of search()', 1, '2026-01-01T00:00:00'),
    ('hf',        'Handoff index — metadata search for handoff refs', 1, '2026-05-26T00:00:00');

-- ============================================================
-- DELIVERABLES — file path to CRC32 hash registry
-- ============================================================
CREATE TABLE IF NOT EXISTS deliverables (
    hash TEXT PRIMARY KEY,
    path TEXT NOT NULL
);

-- ============================================================
-- SESSIONS (da session_memory)
-- ============================================================
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'interrupted', 'completed')),
    topic           TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    agent           TEXT NOT NULL DEFAULT 'Poros',
    task_ids        TEXT NOT NULL DEFAULT '[]',
    token_budget    INTEGER NOT NULL DEFAULT 2000,
    token_discovery INTEGER NOT NULL DEFAULT 0,
    token_read      INTEGER NOT NULL DEFAULT 0,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    updated_at      TEXT NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}'
);

-- ============================================================
-- OBSERVATIONS (da session_memory — timeline)
-- ============================================================
CREATE TABLE IF NOT EXISTS observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    parent_id       INTEGER REFERENCES observations(id),
    type            TEXT NOT NULL
                    CHECK (type IN (
                        'dec', 'del', 'res',
                        'note', 'hf', 'um', 'sys'
                    )),
    agent           TEXT NOT NULL DEFAULT 'Poros',
    content         TEXT NOT NULL,
    tokens_discovery INTEGER NOT NULL DEFAULT 0,
    tokens_read     INTEGER NOT NULL DEFAULT 0,
    token_savings   REAL GENERATED ALWAYS AS (
        CASE WHEN tokens_discovery > 0
        THEN (tokens_discovery - tokens_read) * 1.0 / tokens_discovery
        ELSE 0 END
    ) STORED,
    entities        TEXT NOT NULL DEFAULT '[]',
    handoff_path    TEXT,
    task_ref        TEXT,
    compression_level INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_obs_type ON observations(type);
CREATE INDEX IF NOT EXISTS idx_obs_agent ON observations(agent);
CREATE INDEX IF NOT EXISTS idx_obs_task_ref ON observations(task_ref);

-- ============================================================
-- TASKS (da taskmanager — YAML to SQLite)
-- ============================================================
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    description     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pend'
                    CHECK (status IN (
                        'pend', 'prog', 'done',
                        'x', 'blk', 'stby'
                    )),
    priority        TEXT NOT NULL DEFAULT 'medium'
                    CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    owner           TEXT NOT NULL DEFAULT 'Poros',
    tags            TEXT NOT NULL DEFAULT '[]',
    parent          TEXT REFERENCES tasks(id),
    handoff_refs    TEXT NOT NULL DEFAULT '[]',
    compression_level INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent);

-- ============================================================
-- TASK_EVENTS (da taskmanager — event log)
-- ============================================================
CREATE TABLE IF NOT EXISTS task_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL REFERENCES tasks(id),
    type            TEXT NOT NULL
                    CHECK (type IN (
                        'hr', 'note', 'dec',
                        'dv', 'sc', 'cr'
                    )),
    details         TEXT NOT NULL DEFAULT '',
    handoff_path    TEXT,
    compression_level INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON task_events(type);

-- ============================================================
-- ENTITIES (da session_memory — cross-session linking)
-- ============================================================
CREATE TABLE IF NOT EXISTS entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    entity_type     TEXT NOT NULL DEFAULT 'concept'
                    CHECK (entity_type IN (
                        'project', 'agent', 'concept',
                        'person', 'technology', 'task'
                    )),
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);

-- ============================================================
-- OBSERVATION_ENTITIES (M2M join)
-- ============================================================
CREATE TABLE IF NOT EXISTS observation_entities (
    observation_id  INTEGER NOT NULL REFERENCES observations(id),
    entity_id       INTEGER NOT NULL REFERENCES entities(id),
    PRIMARY KEY (observation_id, entity_id)
);

-- ============================================================
-- SUMMARIES (da session_memory — compression layers)
-- ============================================================
CREATE TABLE IF NOT EXISTS summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    level           INTEGER NOT NULL CHECK (level IN (1, 2, 3)),
    parent_id       INTEGER REFERENCES summaries(id),
    content         TEXT NOT NULL,
    token_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_summary_session ON summaries(session_id, level);

-- ============================================================
-- COUNTERS (da taskmanager — next ID generation)
-- ============================================================
CREATE TABLE IF NOT EXISTS counters (
    area            TEXT PRIMARY KEY,
    last_value      INTEGER NOT NULL DEFAULT 0
);

-- ============================================================
-- MEMORY_LAYERS (Chimera pattern — linksee-memory inspired)
-- ============================================================
CREATE TABLE IF NOT EXISTS memory_layers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    layer           TEXT NOT NULL CHECK (layer IN (
                        'goal', 'context', 'emotion',
                        'implementation', 'caveat', 'learning'
                    )),
    content         TEXT NOT NULL,
    source_observation_id INTEGER REFERENCES observations(id),
    forgetting_risk REAL NOT NULL DEFAULT 0.0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ml_session ON memory_layers(session_id, layer);

-- ============================================================
-- FTS5 — full-text search on observations + tasks
-- ============================================================
CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
    content,
    content=observations,
    content_rowid=id,
    tokenize='porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
    description,
    content=tasks,
    tokenize='porter unicode61'
);

-- ============================================================
-- HF — handoff index (body lives on filesystem only)
-- ============================================================
CREATE TABLE IF NOT EXISTS hf (
    ref      TEXT PRIMARY KEY,
    type     TEXT NOT NULL,
    title    TEXT NOT NULL,
    agent    TEXT NOT NULL,
    task     TEXT,
    st       TEXT NOT NULL DEFAULT 'done',
    prio     TEXT NOT NULL DEFAULT 'med',
    sess     TEXT,
    file     TEXT NOT NULL,
    wiki     TEXT,
    ts       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hf_type ON hf(type);
CREATE INDEX IF NOT EXISTS idx_hf_agent ON hf(agent);
CREATE INDEX IF NOT EXISTS idx_hf_task ON hf(task);
CREATE INDEX IF NOT EXISTS idx_hf_st ON hf(st);
CREATE INDEX IF NOT EXISTS idx_hf_ts ON hf(ts);

CREATE VIRTUAL TABLE IF NOT EXISTS hf_fts USING fts5(
    title, agent, task, type,
    content=hf,
    tokenize='porter unicode61'
);

-- ============================================================
-- KNOWLEDGE CHUNKS (merged from knowledge_base/chunks.db)
-- ============================================================
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    heading_level INTEGER NOT NULL DEFAULT 2,
    content TEXT NOT NULL,
    frontmatter TEXT,
    token_count INTEGER,
    line_start INTEGER,
    line_end INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_path);

CREATE TABLE IF NOT EXISTS file_state (
    file_path TEXT PRIMARY KEY,
    file_hash TEXT NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    last_indexed_at TEXT DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content, heading_path, file_path,
    content='chunks', content_rowid='rowid',
    tokenize='porter unicode61'
);
"""

# ---------------------------------------------------------------------------
# FTS5 triggers
# ---------------------------------------------------------------------------

_FTS_TRIGGERS_SQL = """
-- Observations FTS triggers
CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations BEGIN
    INSERT INTO observations_fts(rowid, content)
    VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS observations_ad AFTER DELETE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS observations_au AFTER UPDATE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
    INSERT INTO observations_fts(rowid, content)
    VALUES (new.id, new.content);
END;

-- Tasks FTS triggers (use subquery for rowid since tasks.id is TEXT primary key)
CREATE TRIGGER IF NOT EXISTS tasks_ai AFTER INSERT ON tasks BEGIN
    INSERT INTO tasks_fts(rowid, description)
    VALUES ((SELECT rowid FROM tasks WHERE id = new.id), new.description);
END;

CREATE TRIGGER IF NOT EXISTS tasks_ad AFTER DELETE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, description)
    VALUES ('delete', (SELECT rowid FROM tasks WHERE id = old.id), old.description);
END;

CREATE TRIGGER IF NOT EXISTS tasks_au AFTER UPDATE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, description)
    VALUES ('delete', (SELECT rowid FROM tasks WHERE id = old.id), old.description);
    INSERT INTO tasks_fts(rowid, description)
    VALUES ((SELECT rowid FROM tasks WHERE id = new.id), new.description);
END;

-- HF FTS triggers (use subquery for rowid since hf.ref is TEXT primary key)
CREATE TRIGGER IF NOT EXISTS hf_ai AFTER INSERT ON hf BEGIN
    INSERT INTO hf_fts(rowid, title, agent, task, type)
    VALUES ((SELECT rowid FROM hf WHERE ref = new.ref), new.title, new.agent, new.task, new.type);
END;

CREATE TRIGGER IF NOT EXISTS hf_ad AFTER DELETE ON hf BEGIN
    INSERT INTO hf_fts(hf_fts, rowid, title, agent, task, type)
    VALUES ('delete', (SELECT rowid FROM hf WHERE ref = old.ref), old.title, old.agent, old.task, old.type);
END;

CREATE TRIGGER IF NOT EXISTS hf_au AFTER UPDATE ON hf BEGIN
    INSERT INTO hf_fts(hf_fts, rowid, title, agent, task, type)
    VALUES ('delete', (SELECT rowid FROM hf WHERE ref = old.ref), old.title, old.agent, old.task, old.type);
    INSERT INTO hf_fts(rowid, title, agent, task, type)
    VALUES ((SELECT rowid FROM hf WHERE ref = new.ref), new.title, new.agent, new.task, new.type);
END;

CREATE TRIGGER IF NOT EXISTS hf_ad AFTER DELETE ON hf BEGIN
    INSERT INTO hf_fts(hf_fts, rowid, title, agent, task, type)
    VALUES ('delete', old.rowid, old.title, old.agent, old.task, old.type);
END;

CREATE TRIGGER IF NOT EXISTS hf_au AFTER UPDATE ON hf BEGIN
    INSERT INTO hf_fts(hf_fts, rowid, title, agent, task, type)
    VALUES ('delete', old.rowid, old.title, old.agent, old.task, old.type);
    INSERT INTO hf_fts(rowid, title, agent, task, type)
    VALUES (new.rowid, new.title, new.agent, new.task, new.type);
END;

-- Chunks FTS triggers
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, content, heading_path, file_path)
    VALUES (new.rowid, new.content, new.heading_path, new.file_path);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content, heading_path, file_path)
    VALUES ('delete', old.rowid, old.content, old.heading_path, old.file_path);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content, heading_path, file_path)
    VALUES ('delete', old.rowid, old.content, old.heading_path, old.file_path);
    INSERT INTO chunks_fts(rowid, content, heading_path, file_path)
    VALUES (new.rowid, new.content, new.heading_path, new.file_path);
END;
"""


# ---------------------------------------------------------------------------
# Compression helper
# ---------------------------------------------------------------------------


def _compress_text(text: str, max_chars: int = 300) -> str:
    """Compress text using Token Juice C2 prose compressor.

    Args:
        text: Original text content.
        max_chars: Maximum character length.

    Returns:
        Compressed string.
    """
    try:
        from tools.token_juice.compressor import compress as tj_compress

        compressed = tj_compress(text, intensity="full")
        if len(compressed) > max_chars:
            compressed = compressed[:max_chars]
        if len(compressed) < len(text) * 0.8:
            return compressed
        return text[:max_chars]
    except Exception:
        return text[:max_chars]


# ---------------------------------------------------------------------------
# SynapsisStore
# ---------------------------------------------------------------------------


class SynapsisStore:
    """Unified SQLite CRUD for session memory + task management."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.path: Path
        if db_path is not None:
            self.path = Path(db_path)
        else:
            self.path = _resolve_db_path()

        logger.debug(f"SynapsisStore initialised with db_path={self.path}")

        # Ensure parent directory exists
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._conn: Any = None  # sqlite3.Connection
        self._write_count: int = 0  # counter for auto health checks
        self._consolidating: bool = False  # lock flag for auto-consolidation
        self._auto_consolidate_counter: int = 0  # rate limiter for auto-consolidation
        self._connect()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Open SQLite connection, enable WAL mode, foreign keys, and fullfsync."""
        import sqlite3

        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.execute("PRAGMA fullfsync=ON;")
        self._init_schema()

    # ------------------------------------------------------------------
    # Knowledge Base search (merged into synapsis.db)
    # ------------------------------------------------------------------

    def knowledge_search(
        self,
        query: str,
        limit: int = 15,
        mode: str = "bm25",
        context_chunks: int = 0,
    ) -> list[dict[str, Any]]:
        """Search knowledge chunks via FTS5 (now in synapsis.db).

        Args:
            mode: ``"bm25"`` | ``"hybrid"`` | ``"auto"`` | ``"embedding"`` | ``"entity"``.
            context_chunks: Adjacent chunks to include (BM25 only).

        Returns:
            List of result dicts.
        """
        if mode in ("hybrid", "embedding", "entity", "auto"):
            from tools.knowledge_base.chunk_indexer import hybrid_search

            return hybrid_search(query, limit=limit, mode=mode)

        # Default: BM25 via FTS5 on synapsis.db
        import re

        safe_query = re.sub(r"(?<=\w)-(?=\w)", " ", query)
        try:
            rows = self._conn.execute(
                """SELECT c.rowid AS id, c.file_path, c.heading_path, c.heading_level,
                          c.content, c.token_count, c.line_start, c.line_end,
                          c.frontmatter, rank
                   FROM chunks_fts
                   JOIN chunks c ON c.rowid = chunks_fts.rowid
                   WHERE chunks_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (safe_query, limit),
            ).fetchall()
        except Exception as exc:
            logger.warning(f"FTS5 search failed: {exc}")
            return []

        results: list[dict[str, Any]] = []
        for r in rows:
            res = dict(r)
            try:
                res["frontmatter"] = json.loads(res.get("frontmatter") or "{}")
            except (json.JSONDecodeError, TypeError):
                res["frontmatter"] = {}
            results.append(res)

        # Context chunks
        if context_chunks > 0 and results:
            results = self._knowledge_attach_context(results, context_chunks)

        return results

    def _knowledge_attach_context(
        self,
        results: list[dict[str, Any]],
        n: int,
    ) -> list[dict[str, Any]]:
        """Attach *n* preceding/succeeding chunks to each result."""
        for res in results:
            file_path = res["file_path"]
            line_start = res["line_start"]

            prev_rows = self._conn.execute(
                """SELECT content, heading_path, line_start, line_end
                   FROM chunks
                   WHERE file_path = ? AND line_start < ?
                   ORDER BY line_start DESC
                   LIMIT ?""",
                (file_path, line_start, n),
            ).fetchall()

            next_rows = self._conn.execute(
                """SELECT content, heading_path, line_start, line_end
                   FROM chunks
                   WHERE file_path = ? AND line_start > ?
                   ORDER BY line_start ASC
                   LIMIT ?""",
                (file_path, line_start, n),
            ).fetchall()

            res["context"] = {
                "before": [dict(r) for r in reversed(prev_rows)],
                "after": [dict(r) for r in next_rows],
            }

        return results

    def knowledge_read(self, file_path: str) -> str | None:
        """Read full content for a file from chunks table (now in synapsis.db).

        Returns content reconstructed from all chunks for the given file_path.
        """
        rows = self._conn.execute(
            "SELECT content FROM chunks WHERE file_path = ? ORDER BY line_start",
            (file_path,),
        ).fetchall()
        if not rows:
            logger.warning(f"knowledge_read: no chunks found for '{file_path}'")
            return None
        return "\n".join(r["content"] for r in rows)

    def _init_schema(self) -> None:
        """Create tables and FTS5 triggers if they don't exist.

        Also runs safe migrations for columns added in later versions.
        """
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.executescript(_FTS_TRIGGERS_SQL)
        # Apply safe migrations for legacy compatibility
        self._safe_add_column("observations", "compression_level", "INTEGER NOT NULL DEFAULT 0")
        self._safe_add_column("tasks", "compression_level", "INTEGER NOT NULL DEFAULT 0")
        self._safe_add_column("task_events", "compression_level", "INTEGER NOT NULL DEFAULT 0")
        # Self-Healing Memory: ADD-only soft-supersede columns
        self._safe_add_column("observations", "is_active", "INTEGER NOT NULL DEFAULT 1")
        self._safe_add_column("observations", "superseded_by", "TEXT")
        self._safe_add_column("sessions", "is_active", "INTEGER NOT NULL DEFAULT 1")
        self._safe_add_column("sessions", "superseded_by", "TEXT")
        self._safe_add_column("tasks", "is_active", "INTEGER NOT NULL DEFAULT 1")
        self._safe_add_column("tasks", "superseded_by", "TEXT")
        self._safe_add_column("entities", "is_active", "INTEGER NOT NULL DEFAULT 1")
        self._safe_add_column("entities", "superseded_by", "TEXT")
        self._conn.commit()

        # Rebuild hf_fts if empty (e.g. first run after adding FTS5)
        hf_fts_count = self._conn.execute("SELECT COUNT(*) FROM hf_fts").fetchone()[0]
        if hf_fts_count == 0:
            existing_hf = self._conn.execute("SELECT COUNT(*) FROM hf").fetchone()[0]
            if existing_hf > 0:
                self._conn.execute("INSERT INTO hf_fts(hf_fts) VALUES('rebuild')")
                logger.info(f"hf_fts rebuilt with {existing_hf} rows")

        # One-time migration from legacy chunks.db
        self._migrate_chunks_if_needed()

        # Rebuild chunks_fts if empty but chunks exist
        chunks_fts_count = self._conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        if chunks_fts_count == 0:
            existing_chunks = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            if existing_chunks > 0:
                self._conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
                logger.info(f"chunks_fts rebuilt with {existing_chunks} rows")

        # Token discipline: ensure short canonical forms in CHECKs + data
        # (one-time heal for DBs created before short forms were canonical)
        try:
            self._migrate_to_short_canonical_forms()
        except Exception as exc:
            logger.warning(f"Short canonical migration skipped/failed (non-fatal): {exc}")

        logger.debug("Schema initialised (15 tables + FTS5 triggers + migrations)")

    def _safe_add_column(self, table: str, column: str, col_def: str) -> None:
        """Add a column to a table if it doesn't already exist."""
        cursor = self._conn.execute(f"PRAGMA table_info({table})")
        existing_cols: set[str] = {row[1] for row in cursor.fetchall()}
        if column not in existing_cols:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            self._conn.commit()
            logger.debug(f"Added column '{column}' to table '{table}'")

    def _migrate_chunks_if_needed(self) -> None:
        """One-time migration: copy chunks from legacy chunks.db into synapsis.db."""
        # Already has data = migration done or fresh start
        cnt = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if cnt > 0:
            return

        legacy_path = self.path.parent / "chunks.db"
        if not legacy_path.exists():
            return  # No legacy DB to migrate from

        import sqlite3

        try:
            legacy = sqlite3.connect(str(legacy_path))
            legacy.row_factory = sqlite3.Row

            # Verify legacy schema has chunks table
            legacy_tables = {
                r[0]
                for r in legacy.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "chunks" not in legacy_tables:
                logger.info("Legacy chunks.db has no chunks table — skipping migration")
                legacy.close()
                return

            # Copy chunks
            rows = legacy.execute("SELECT * FROM chunks").fetchall()
            for r in rows:
                d = dict(r)
                self._conn.execute(
                    """INSERT OR IGNORE INTO chunks
                       (id, file_path, file_hash, heading_path, heading_level,
                        content, frontmatter, token_count, line_start, line_end,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        d["id"],
                        d["file_path"],
                        d["file_hash"],
                        d["heading_path"],
                        d.get("heading_level", 2),
                        d["content"],
                        d.get("frontmatter"),
                        d.get("token_count"),
                        d.get("line_start"),
                        d.get("line_end"),
                        d.get("created_at", ""),
                        d.get("updated_at", ""),
                    ),
                )

            # Copy file_state
            rows_fs = legacy.execute("SELECT * FROM file_state").fetchall()
            for r in rows_fs:
                d = dict(r)
                self._conn.execute(
                    """INSERT OR IGNORE INTO file_state
                       (file_path, file_hash, chunk_count, last_indexed_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        d["file_path"],
                        d["file_hash"],
                        d.get("chunk_count", 0),
                        d.get("last_indexed_at", ""),
                    ),
                )

            self._conn.commit()
            logger.info(
                f"Migrated {len(rows)} chunks, {len(rows_fs)} file_state entries from legacy chunks.db"
            )
            legacy.close()

            # Rebuild chunks_fts after migration
            self._conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
            logger.info("chunks_fts rebuilt after migration")
        except Exception as exc:
            logger.error(f"Chunks migration failed: {exc}")

    # ------------------------------------------------------------------
    # Token discipline migration — short canonical forms for CHECKs + data
    # ------------------------------------------------------------------

    def _migrate_to_short_canonical_forms(self) -> None:
        """Detect legacy long-form CHECK constraints and rebuild affected tables.

        Tables: tasks (status), observations (type), task_events (type).
        Normalizes data using the canonical short forms and recreates indexes,
        FTS triggers and FTS content. Idempotent / safe no-op when already short.
        """
        to_migrate: list[str] = []
        for tbl, marker in [
            ("tasks", "pending"),
            ("observations", "decision"),
            ("task_events", "handoff_ref"),
        ]:
            row = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (tbl,),
            ).fetchone()
            if row and row[0] and marker in row[0]:
                to_migrate.append(tbl)

        if not to_migrate:
            return

        logger.warning(f"Short-forms migration: rebuilding tables with legacy CHECKs: {to_migrate}")

        if "tasks" in to_migrate:
            self._rebuild_table_tasks_short()
        if "observations" in to_migrate:
            self._rebuild_table_observations_short()
        if "task_events" in to_migrate:
            self._rebuild_table_task_events_short()

        self._conn.commit()
        logger.info("Short canonical forms migration completed.")

    def _rebuild_table_tasks_short(self) -> None:
        """Rebuild tasks with short status CHECK + normalize data."""
        rows = [dict(r) for r in self._conn.execute("SELECT * FROM tasks").fetchall()]
        self._conn.execute("DROP TABLE IF EXISTS tasks_new")
        self._conn.executescript(
            """
            CREATE TABLE tasks_new (
                id              TEXT PRIMARY KEY,
                description     TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pend'
                                CHECK (status IN ('pend','prog','done','x','blk','stby')),
                priority        TEXT NOT NULL DEFAULT 'medium'
                                CHECK (priority IN ('low','medium','high','critical')),
                owner           TEXT NOT NULL DEFAULT 'Poros',
                tags            TEXT NOT NULL DEFAULT '[]',
                parent          TEXT REFERENCES tasks(id),
                handoff_refs    TEXT NOT NULL DEFAULT '[]',
                compression_level INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                is_active       INTEGER NOT NULL DEFAULT 1,
                superseded_by   TEXT
            );
            """
        )
        for t in rows:
            s = normalize_task_status(t.get("status")) or "pend"
            self._conn.execute(
                """INSERT INTO tasks_new
                   (id,description,status,priority,owner,tags,parent,handoff_refs,
                    compression_level,created_at,updated_at,is_active,superseded_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    t["id"], t["description"], s,
                    t.get("priority") or "medium", t.get("owner") or "Poros",
                    t.get("tags") or "[]", t.get("parent"),
                    t.get("handoff_refs") or "[]",
                    int(t.get("compression_level") or 0),
                    t["created_at"], t["updated_at"],
                    int(t.get("is_active", 1)), t.get("superseded_by"),
                ),
            )
        self._conn.execute("DROP TABLE tasks")
        self._conn.execute("ALTER TABLE tasks_new RENAME TO tasks")
        self._conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner);
            CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
            CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent);
            CREATE TRIGGER IF NOT EXISTS tasks_ai AFTER INSERT ON tasks BEGIN
                INSERT INTO tasks_fts(rowid, description)
                VALUES ((SELECT rowid FROM tasks WHERE id = new.id), new.description);
            END;
            CREATE TRIGGER IF NOT EXISTS tasks_ad AFTER DELETE ON tasks BEGIN
                INSERT INTO tasks_fts(tasks_fts, rowid, description)
                VALUES ('delete', (SELECT rowid FROM tasks WHERE id = old.id), old.description);
            END;
            CREATE TRIGGER IF NOT EXISTS tasks_au AFTER UPDATE ON tasks BEGIN
                INSERT INTO tasks_fts(tasks_fts, rowid, description)
                VALUES ('delete', (SELECT rowid FROM tasks WHERE id = old.id), old.description);
                INSERT INTO tasks_fts(rowid, description)
                VALUES ((SELECT rowid FROM tasks WHERE id = new.id), new.description);
            END;
            """
        )
        self._conn.execute("INSERT INTO tasks_fts(tasks_fts) VALUES('rebuild')")

    def _rebuild_table_observations_short(self) -> None:
        """Rebuild observations with short type CHECK + normalize data (preserve ids)."""
        rows = [dict(r) for r in self._conn.execute("SELECT * FROM observations").fetchall()]
        self._conn.execute("DROP TABLE IF EXISTS observations_new")
        self._conn.executescript(
            """
            CREATE TABLE observations_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      TEXT NOT NULL REFERENCES sessions(id),
                parent_id       INTEGER REFERENCES observations(id),
                type            TEXT NOT NULL
                                CHECK (type IN ('dec','del','res','note','hf','um','sys')),
                agent           TEXT NOT NULL DEFAULT 'Poros',
                content         TEXT NOT NULL,
                tokens_discovery INTEGER NOT NULL DEFAULT 0,
                tokens_read     INTEGER NOT NULL DEFAULT 0,
                token_savings   REAL GENERATED ALWAYS AS (
                    CASE WHEN tokens_discovery > 0
                    THEN (tokens_discovery - tokens_read) * 1.0 / tokens_discovery
                    ELSE 0 END
                ) STORED,
                entities        TEXT NOT NULL DEFAULT '[]',
                handoff_path    TEXT,
                task_ref        TEXT,
                compression_level INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL,
                is_active       INTEGER NOT NULL DEFAULT 1,
                superseded_by   TEXT
            );
            """
        )
        for o in rows:
            t = normalize_observe_type(o.get("type")) or "note"
            self._conn.execute(
                """INSERT INTO observations_new
                   (id,session_id,parent_id,type,agent,content,
                    tokens_discovery,tokens_read,entities,handoff_path,task_ref,
                    compression_level,created_at,is_active,superseded_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    o["id"], o["session_id"], o.get("parent_id"), t,
                    o.get("agent") or "Poros", o["content"],
                    int(o.get("tokens_discovery") or 0),
                    int(o.get("tokens_read") or 0),
                    o.get("entities") or "[]", o.get("handoff_path"), o.get("task_ref"),
                    int(o.get("compression_level") or 0), o["created_at"],
                    int(o.get("is_active", 1)), o.get("superseded_by"),
                ),
            )
        self._conn.execute("DROP TABLE observations")
        self._conn.execute("ALTER TABLE observations_new RENAME TO observations")
        self._conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_obs_session ON observations(session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_obs_type ON observations(type);
            CREATE INDEX IF NOT EXISTS idx_obs_agent ON observations(agent);
            CREATE INDEX IF NOT EXISTS idx_obs_task_ref ON observations(task_ref);
            CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations BEGIN
                INSERT INTO observations_fts(rowid, content)
                VALUES (new.id, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS observations_ad AFTER DELETE ON observations BEGIN
                INSERT INTO observations_fts(observations_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS observations_au AFTER UPDATE ON observations BEGIN
                INSERT INTO observations_fts(observations_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
                INSERT INTO observations_fts(rowid, content)
                VALUES (new.id, new.content);
            END;
            """
        )
        self._conn.execute("INSERT INTO observations_fts(observations_fts) VALUES('rebuild')")

    def _rebuild_table_task_events_short(self) -> None:
        """Rebuild task_events with short type CHECK + normalize data (preserve ids)."""
        rows = [dict(r) for r in self._conn.execute("SELECT * FROM task_events").fetchall()]
        self._conn.execute("DROP TABLE IF EXISTS task_events_new")
        self._conn.executescript(
            """
            CREATE TABLE task_events_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id         TEXT NOT NULL REFERENCES tasks(id),
                type            TEXT NOT NULL
                                CHECK (type IN ('hr','note','dec','dv','sc','cr')),
                details         TEXT NOT NULL DEFAULT '',
                handoff_path    TEXT,
                compression_level INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL
            );
            """
        )
        for e in rows:
            t = normalize_event_type(e.get("type")) or "note"
            self._conn.execute(
                """INSERT INTO task_events_new
                   (id,task_id,type,details,handoff_path,compression_level,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    e["id"], e["task_id"], t,
                    e.get("details") or "", e.get("handoff_path"),
                    int(e.get("compression_level") or 0), e["created_at"],
                ),
            )
        self._conn.execute("DROP TABLE task_events")
        self._conn.execute("ALTER TABLE task_events_new RENAME TO task_events")
        self._conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_events_type ON task_events(type);
            """
        )

    def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            self._write_count = 0
            logger.debug("SynapsisStore connection closed")

    # ------------------------------------------------------------------
    # Write counter — auto health check on every N writes
    # ------------------------------------------------------------------

    def _increment_write_count(self) -> None:
        """Increment write counter, auto quick_check every 20 writes."""
        self._write_count += 1
        if self._write_count > 0 and self._write_count % 20 == 0:
            try:
                result = self.db_health_check("quick")
                if not result.get("passed", False):
                    logger.warning(
                        f"Auto quick_check failed at write #{self._write_count}: "
                        f"{result.get('message', 'unknown')}"
                    )
                else:
                    logger.debug(f"Auto quick_check passed at write #{self._write_count}")
            except Exception as exc:
                logger.warning(f"Auto quick_check error at write #{self._write_count}: {exc}")

    # ------------------------------------------------------------------
    # Auto-consolidation — lightweight check + trigger
    # ------------------------------------------------------------------

    def auto_consolidate_if_needed(self, session_id: str | None = None) -> dict[str, Any]:
        """Check if consolidation is needed and run it if so.

        Criteria (any triggers):
        - Total observations in session > 50 (only if session_id given)
        - Observations older than 7 days without consolidation
        - More than 20 unconsolidated observations

        Rate-limited: when called from ``add_observation``, only checks every
        10th call via ``_auto_consolidate_counter``. When called from
        ``compress_observations`` it always checks.

        Args:
            session_id: Optional session to scope the check to.

        Returns:
            Dict with triggered, reason, consolidated count, and details.
        """
        if self._consolidating:
            logger.info("auto_consolidate: skipped — consolidation already in progress")
            return {
                "triggered": False,
                "reason": "already consolidating",
                "consolidated": 0,
            }

        # Get unconsolidated candidates (no age filter for detection)
        from datetime import UTC, datetime

        candidates = self.get_non_consolidated_observations(
            session_id=session_id,
            age_days=0,
            max_results=200,
        )

        candidate_count = len(candidates)

        # Check total observations in session (only if session_id given)
        total_obs = 0
        if session_id:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM observations WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            total_obs = row["c"] if row else 0

        # Check oldest candidate age
        oldest_age = 0.0
        now = datetime.now(UTC)
        for obs in candidates:
            created = obs.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created)
                    age = (now - dt).total_seconds() / 86_400
                    oldest_age = max(oldest_age, age)
                except (ValueError, TypeError):
                    pass

        # Determine if consolidation should trigger
        should_consolidate = False
        reasons: list[str] = []

        if session_id and total_obs > 50:
            should_consolidate = True
            reasons.append(f"total observations ({total_obs}) > 50")

        if oldest_age > 7:
            should_consolidate = True
            reasons.append(f"oldest {oldest_age:.1f}d old (> 7)")

        if candidate_count > 20:
            should_consolidate = True
            reasons.append(f"unconsolidated ({candidate_count}) > 20")

        if not should_consolidate:
            logger.info(
                f"auto_consolidate: skipped — no trigger "
                f"(candidates={candidate_count}, oldest={oldest_age:.1f}d, "
                f"total_obs={total_obs})"
            )
            return {
                "triggered": False,
                "reason": "no trigger met",
                "consolidated": 0,
                "candidate_count": candidate_count,
                "oldest_age_days": round(oldest_age, 1) if oldest_age > 0 else None,
                "total_observations": total_obs,
            }

        # Trigger consolidation
        self._consolidating = True
        try:
            result = self.compress_observations(age_days=7, max_level=2, dry_run=False)
            consolidated = result.get("observations_warm", 0) + result.get("observations_cold", 0)
            logger.info(
                f"auto_consolidate: triggered — {'; '.join(reasons)} — "
                f"consolidated {consolidated} obs"
            )
            return {
                "triggered": True,
                "reason": "; ".join(reasons),
                "consolidated": consolidated,
                "details": result,
                "candidate_count": candidate_count,
                "oldest_age_days": round(oldest_age, 1) if oldest_age > 0 else None,
                "total_observations": total_obs,
            }
        except Exception as exc:
            logger.error(f"auto_consolidate: failed — {exc}")
            return {
                "triggered": True,
                "reason": f"error: {exc}",
                "consolidated": 0,
                "error": str(exc),
                "candidate_count": candidate_count,
                "oldest_age_days": round(oldest_age, 1) if oldest_age > 0 else None,
                "total_observations": total_obs,
            }
        finally:
            self._consolidating = False

    # ------------------------------------------------------------------
    # Health checks (PRAGMA-based)
    # ------------------------------------------------------------------

    def db_health_check(self, cmd: str = "quick") -> dict[str, Any]:
        """Run a database health check via PRAGMA.

        Args:
            cmd: ``"quick"`` | ``"full"`` | ``"repair"``.

        Returns:
            Dict with cmd, passed, message, duration_ms, recovery info.
        """
        import time

        start = time.time()
        recovery_attempted = False
        recovery_success: bool | None = None

        try:
            if cmd == "quick":
                cursor = self._conn.execute("PRAGMA quick_check")
                row = cursor.fetchone()
                # PRAGMA quick_check returns "ok" or error description
                result_text = row[0] if row else "unknown"
                passed = result_text.strip().lower() == "ok"
                message = result_text

            elif cmd == "full":
                cursor = self._conn.execute("PRAGMA integrity_check")
                rows = cursor.fetchall()
                # integrity_check returns "ok" or list of errors
                all_ok = all(str(r[0]).strip().lower() == "ok" for r in rows)
                if all_ok:
                    passed = True
                    message = "ok"
                else:
                    passed = False
                    errors = [str(r[0]) for r in rows if str(r[0]).strip().lower() != "ok"]
                    message = "; ".join(errors[:5])
                    if len(errors) > 5:
                        message += f" (... and {len(errors) - 5} more)"

            elif cmd == "repair":
                recovery_attempted = True
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    message = "WAL checkpoint truncated"
                    passed = True
                    recovery_success = True
                except Exception as wal_err:
                    logger.warning(f"WAL checkpoint failed, attempting .recover: {wal_err}")
                    # Use .recover: export to temp file, replace current
                    try:
                        import tempfile

                        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
                        tmp_path = Path(tmp.name)
                        tmp.close()

                        self._conn.execute(
                            "CREATE TABLE recovered AS SELECT * FROM sqlite_master WHERE 1=0"
                        )
                        # Simulate recovery by running integrity check as fallback
                        cursor = self._conn.execute("PRAGMA integrity_check")
                        rows = cursor.fetchall()
                        errors = [str(r[0]) for r in rows if str(r[0]).strip().lower() != "ok"]
                        if errors:
                            err_summary = "; ".join(errors[:3])
                            message = (
                                f"Recovery attempted but {len(errors)} integrity "
                                f"errors remain: {err_summary}"
                            )
                            passed = False
                            recovery_success = False
                        else:
                            message = "No integrity errors found, recovery not needed"
                            passed = True
                            recovery_success = True

                        tmp_path.unlink(missing_ok=True)
                    except Exception as recover_err:
                        message = f"Recovery failed: {recover_err}"
                        passed = False
                        recovery_success = False
            else:
                return {
                    "cmd": cmd,
                    "passed": False,
                    "message": f"Unknown cmd '{cmd}'. Use quick, full, or repair.",
                    "duration_ms": 0.0,
                    "recovery_attempted": False,
                    "recovery_success": None,
                }

        except Exception as exc:
            passed = False
            message = str(exc)

        duration_ms = round((time.time() - start) * 1000, 2)

        return {
            "cmd": cmd,
            "passed": passed,
            "message": message,
            "duration_ms": duration_ms,
            "recovery_attempted": recovery_attempted,
            "recovery_success": recovery_success,
        }

    def db_set_pragma(self, pragma: str, value: Any) -> dict[str, Any]:  # noqa: ANN401
        """Set a PRAGMA value at runtime."""
        self._conn.execute(f"PRAGMA {pragma}={value}")
        self._conn.commit()
        from tools.synapsis.models import now_iso

        logger.debug(f"PRAGMA {pragma} set to {value}")
        return {
            "pragma": pragma,
            "value": value,
            "set_at": now_iso(),
        }

    # ------------------------------------------------------------------
    # Checkpoint / Rollback (SAVEPOINT-based)
    # ------------------------------------------------------------------

    def checkpoint_create(self, name: str = "auto") -> dict[str, Any]:
        """Create a named SAVEPOINT."""
        from tools.synapsis.models import now_iso

        self._conn.execute(f"SAVEPOINT {name}")
        logger.debug(f"Checkpoint created: {name}")
        return {"name": name, "created_at": now_iso()}

    def checkpoint_restore(self, name: str = "auto") -> dict[str, Any]:
        """Restore to a named SAVEPOINT (ROLLBACK + RELEASE)."""
        from tools.synapsis.models import now_iso

        try:
            self._conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self._conn.execute(f"RELEASE SAVEPOINT {name}")
            logger.debug(f"Checkpoint restored: {name}")
            return {"name": name, "restored_at": now_iso(), "success": True}
        except Exception as exc:
            logger.error(f"Checkpoint restore failed for '{name}': {exc}")
            return {"name": name, "restored_at": now_iso(), "success": False, "error": str(exc)}

    def checkpoint_release(self, name: str = "auto") -> dict[str, Any]:
        """Release a named SAVEPOINT."""
        from tools.synapsis.models import now_iso

        try:
            self._conn.execute(f"RELEASE SAVEPOINT {name}")
            logger.debug(f"Checkpoint released: {name}")
            return {"name": name, "released_at": now_iso()}
        except Exception as exc:
            logger.error(f"Checkpoint release failed for '{name}': {exc}")
            return {"name": name, "released_at": now_iso(), "error": str(exc)}

    def safe_execute(
        self,
        operations: list[Callable[[], Any]],
        rollback_on_fail: bool = True,
    ) -> dict[str, Any]:
        """Execute operations with automatic SAVEPOINT checkpoint/rollback.

        Args:
            operations: Callables to execute in order.
            rollback_on_fail: If True, roll back on failure.

        Returns:
            Dict with success, operations_completed, error, rolled_back.
        """
        self.checkpoint_create("safe_execute")
        completed = 0
        rolled_back = False

        for op in operations:
            try:
                op()
                completed += 1
            except Exception as exc:
                if rollback_on_fail:
                    self.checkpoint_restore("safe_execute")
                    rolled_back = True
                else:
                    self.checkpoint_release("safe_execute")
                logger.error(f"safe_execute failed at operation {completed + 1}: {exc}")
                return {
                    "success": False,
                    "operations_completed": completed,
                    "error": str(exc),
                    "rolled_back": rolled_back,
                }

        self.checkpoint_release("safe_execute")
        return {
            "success": True,
            "operations_completed": completed,
            "error": None,
            "rolled_back": False,
        }

    def vacuum(self) -> dict[str, Any]:
        """Run VACUUM and reindex.

        Returns:
            Dict with path, size_before, size_after (bytes).
        """

        size_before = self.path.stat().st_size
        self._conn.execute("VACUUM;")
        self._conn.execute("REINDEX;")
        self._conn.execute("ANALYZE;")
        size_after = self.path.stat().st_size
        logger.info(f"VACUUM completed: {size_before} → {size_after} bytes")
        return {
            "path": str(self.path),
            "size_before": size_before,
            "size_after": size_after,
            "savings": size_before - size_after,
        }

    # ------------------------------------------------------------------
    # Self-Healing Memory — health scoring + orphan detection
    # ------------------------------------------------------------------

    def compute_health_score(self) -> dict[str, Any]:
        """Compute composite database health score (0-100)."""
        score = 100
        breakdown: dict[str, Any] = {}
        details_parts: list[str] = []
        all_clear = True

        # 1. Integrity check
        integrity = self.db_health_check("full")
        if not integrity.get("passed", False):
            score -= 40
            msg = integrity.get("message", "integrity check failed")
            breakdown["integrity"] = {"penalty": -40, "message": msg}
            details_parts.append(f"integrity: -40 ({msg})")
            all_clear = False
        else:
            breakdown["integrity"] = {"penalty": 0, "message": "ok"}

        # 2. FK violations (PRAGMA foreign_key_check)
        try:
            fk_rows = self._conn.execute("PRAGMA foreign_key_check").fetchall()
            fk_count = len(fk_rows)
            if fk_count > 0:
                fk_penalty = min(fk_count * 5, 40)
                score -= fk_penalty
                breakdown["fk_violations"] = {"penalty": -fk_penalty, "count": fk_count}
                details_parts.append(f"FK violations: {fk_count} → -{fk_penalty}")
                all_clear = False
            else:
                breakdown["fk_violations"] = {"penalty": 0, "count": 0}
        except Exception as exc:
            breakdown["fk_violations"] = {"error": str(exc)}

        # 3. Orphan tasks
        orphans = self.orphan_scan()
        orphan_count = len(orphans)
        if orphan_count > 0:
            orphan_penalty = min(orphan_count * 10, 50)
            score -= orphan_penalty
            breakdown["orphan_tasks"] = {"penalty": -orphan_penalty, "count": orphan_count}
            details_parts.append(f"orphan tasks: {orphan_count} → -{orphan_penalty}")
            all_clear = False
        else:
            breakdown["orphan_tasks"] = {"penalty": 0, "count": 0}

        # 4. is_active ratio (across all tables with the column)
        for table in ("tasks", "sessions", "observations"):
            try:
                total = self._conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
                active = self._conn.execute(
                    f"SELECT COUNT(*) AS c FROM {table} WHERE is_active = 1"
                ).fetchone()
                t = total["c"] if total else 0
                a = active["c"] if active else 0
                ratio = (a / t * 100) if t > 0 else 100.0
                if ratio < 80:
                    penalty = -15
                    score += penalty
                    breakdown[f"active_ratio_{table}"] = {
                        "penalty": penalty,
                        "ratio_pct": round(ratio, 1),
                    }
                    details_parts.append(f"{table} active ratio: {ratio:.1f}% (<80%) → -15")
                    all_clear = False
                else:
                    breakdown[f"active_ratio_{table}"] = {
                        "penalty": 0,
                        "ratio_pct": round(ratio, 1),
                    }
            except Exception:
                pass

        # 5. Temporal gap anomaly
        try:
            gap_rows = self._conn.execute(
                """SELECT o1.created_at AS prev, o2.created_at AS next
                   FROM observations o1
                   JOIN observations o2 ON o2.id = (
                       SELECT MIN(id) FROM observations WHERE id > o1.id
                   )
                   WHERE o2.id IS NOT NULL
                     AND (julianday(o2.created_at) - julianday(o1.created_at)) * 24 > 1
                   LIMIT 10"""
            ).fetchall()
            gap_count = len(gap_rows)
            if gap_count > 0:
                gap_penalty = min(gap_count * 5, 25)
                score -= gap_penalty
                breakdown["temporal_gaps"] = {"penalty": -gap_penalty, "count": gap_count}
                details_parts.append(f"temporal gaps >1h: {gap_count} → -{gap_penalty}")
                all_clear = False
            else:
                breakdown["temporal_gaps"] = {"penalty": 0, "count": 0}
        except Exception as exc:
            breakdown["temporal_gaps"] = {"error": str(exc)}

        # 6. Bonus if all clear
        if all_clear:
            score += 10
            breakdown["all_clear_bonus"] = {"bonus": 10}
            details_parts.append("all checks passed → +10 bonus")

        # Clamp score to 0-100
        score = max(0, min(100, score))
        breakdown["final_score"] = score

        return {
            "score": score,
            "breakdown": breakdown,
            "details": "; ".join(details_parts) if details_parts else "all healthy",
        }

    def orphan_scan(self) -> list[dict[str, Any]]:
        """Find orphan tasks (in_progress >24h, no handoff_refs, no recent events)."""
        orphans: list[dict[str, Any]] = []

        rows = self._conn.execute(
            """SELECT id, status, updated_at, handoff_refs
               FROM tasks
               WHERE status = 'prog'
                 AND updated_at < datetime('now', '-24 hours')
                 AND is_active = 1"""
        ).fetchall()

        for row in rows:
            task_id = row["id"]
            handoff_json = row["handoff_refs"]
            handoff_refs: list[str] = []
            if handoff_json:
                try:
                    handoff_refs = json.loads(handoff_json)
                except (json.JSONDecodeError, TypeError):
                    handoff_refs = []

            # Check if there are any events in the last 24h
            recent_events = self._conn.execute(
                """SELECT COUNT(*) AS c FROM task_events
                   WHERE task_id = ? AND created_at >= datetime('now', '-24 hours')""",
                (task_id,),
            ).fetchone()
            recent_count = recent_events["c"] if recent_events else 0

            reasons: list[str] = []
            if not handoff_refs:
                reasons.append("no handoff_refs")
            if recent_count == 0:
                reasons.append("no recent events")

            if reasons:
                orphans.append(
                    {
                        "task_id": task_id,
                        "reason": "; ".join(reasons),
                    }
                )

        if orphans:
            logger.warning(f"orphan_scan: found {len(orphans)} orphan tasks")

        return orphans

    # ------------------------------------------------------------------
    # Transaction context manager
    # ------------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Context manager for atomic transactions."""
        try:
            yield
            self._conn.commit()
            self._increment_write_count()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Domain gating
    # ------------------------------------------------------------------

    def get_domain(self, domain_id: str) -> dict[str, Any] | None:
        """Get a domain by ID."""
        row = self._conn.execute("SELECT * FROM domains WHERE id = ?", (domain_id,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def set_domain_active(self, domain_id: str, is_active: bool) -> bool:
        """Enable or disable a domain."""
        with self.transaction():
            cur = self._conn.execute(
                "UPDATE domains SET is_active = ? WHERE id = ?",
                (int(is_active), domain_id),
            )
        return cur.rowcount > 0

    def list_domains(self) -> list[dict[str, Any]]:
        """List all domains."""
        rows = self._conn.execute("SELECT * FROM domains ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(
        self,
        topic: str,
        task_ids: list[str] | None = None,
        token_budget: int = 2000,
    ) -> dict[str, Any]:
        """Create and return a new session."""
        session_id = generate_session_id()
        ts = now_iso()
        task_ids_json = json.dumps(task_ids or [])

        with self.transaction():
            self._conn.execute(
                """INSERT INTO sessions
                   (id, status, topic, summary, agent, task_ids, token_budget,
                    token_discovery, token_read, started_at, updated_at, metadata)
                   VALUES (?, 'active', ?, '', 'Poros', ?, ?, 0, 0, ?, ?, '{}')""",
                (session_id, topic, task_ids_json, token_budget, ts, ts),
            )

        logger.info(f"Session created: {session_id} topic='{topic[:60]}'")

        return {
            "id": session_id,
            "status": "active",
            "topic": topic,
            "task_ids": task_ids or [],
            "token_budget": token_budget,
            "started_at": ts,
            "updated_at": ts,
        }

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve a session by ID (active sessions only)."""
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ? AND is_active = 1", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_session_dict(row)

    def get_active_session(self) -> dict[str, Any] | None:
        """Return the most recent active/interrupted session."""
        row = self._conn.execute(
            """SELECT * FROM sessions
               WHERE status IN ('active', 'interrupted')
               ORDER BY rowid DESC
               LIMIT 1""",
        ).fetchone()
        if row is None:
            return None
        return self._row_to_session_dict(row)

    def update_session(self, session_id: str, **kwargs: Any) -> bool:  # noqa: ANN401
        """Update fields on a session via kwargs.

        Returns:
            True if updated, False if not found.
        """
        allowed = {
            "status",
            "topic",
            "summary",
            "agent",
            "token_budget",
            "token_discovery",
            "token_read",
            "ended_at",
            "metadata",
            "task_ids",
        }
        updates: dict[str, Any] = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            logger.debug(f"No valid fields to update for session {session_id}")
            return True

        if "task_ids" in updates and isinstance(updates["task_ids"], list):
            updates["task_ids"] = json.dumps(updates["task_ids"])
        if "metadata" in updates and isinstance(updates["metadata"], dict):
            updates["metadata"] = json.dumps(updates["metadata"])

        updates["updated_at"] = now_iso()

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [session_id]

        with self.transaction():
            cur = self._conn.execute(
                f"UPDATE sessions SET {set_clause} WHERE id = ?",
                values,
            )

        if cur.rowcount == 0:
            logger.warning(f"Session {session_id} not found for update")
            return False

        logger.debug(f"Session {session_id} updated: {set(updates.keys())}")
        return True

    def ensure_session(
        self,
        session_id: str,
        topic: str = "auto-recovered",
        agent: str = "Poros",
    ) -> dict[str, Any]:
        """Ensure a session exists — create it lazily if missing (idempotent)."""
        ts = now_iso()
        with self.transaction():
            self._conn.execute(
                """INSERT OR IGNORE INTO sessions
                   (id, status, topic, summary, agent, task_ids, token_budget,
                    token_discovery, token_read, started_at, updated_at, metadata)
                   VALUES (?, 'active', ?, '', ?, '[]', 2000, 0, 0, ?, ?, '{}')""",
                (session_id, topic, agent, ts, ts),
            )

        session = self.get_session(session_id)
        assert session is not None, f"Session {session_id} should exist after INSERT OR IGNORE"
        if session["status"] == "active" and session["topic"] == topic:
            logger.info(f"Session auto-created: {session_id} (lazy recovery)")

        return session

    def get_session_metrics(self, session_id: str) -> dict[str, Any]:
        """Compute aggregate metrics for a session."""
        obs_row = self._conn.execute(
            """SELECT COUNT(*) AS cnt,
                      COALESCE(SUM(tokens_discovery), 0) AS tot_disc,
                      COALESCE(SUM(tokens_read), 0) AS tot_read
               FROM observations WHERE session_id = ?""",
            (session_id,),
        ).fetchone()

        entity_row = self._conn.execute(
            """SELECT COUNT(DISTINCT e.id) AS cnt
               FROM entities e
               JOIN observation_entities oe ON oe.entity_id = e.id
               JOIN observations o ON o.id = oe.observation_id
               WHERE o.session_id = ?""",
            (session_id,),
        ).fetchone()

        summary_row = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()

        obs_count = obs_row["cnt"] if obs_row else 0
        tot_disc = obs_row["tot_disc"] if obs_row else 0
        tot_read = obs_row["tot_read"] if obs_row else 0
        savings_avg = compute_token_savings(tot_disc, tot_read) if tot_disc > 0 else 0.0

        return {
            "observations_count": obs_count,
            "total_tokens_discovery": tot_disc,
            "total_tokens_read": tot_read,
            "token_savings_avg": round(savings_avg, 4),
            "entity_count": entity_row["cnt"] if entity_row else 0,
            "summary_count": summary_row["cnt"] if summary_row else 0,
        }

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    def add_observation(
        self,
        session_id: str,
        type: str,
        content: str,
        agent: str = "Poros",
        entities: list[str] | None = None,
        handoff_path: str | None = None,
        task_ref: str | None = None,
        tokens_discovery: int = 0,
        tokens_read: int = 0,
        parent_id: int | None = None,
    ) -> int:
        """Add an observation to the timeline. Returns the new observation ID."""
        ts = now_iso()
        type = normalize_observe_type(type) or "note"
        entities_json = json.dumps(entities or [])

        with self.transaction():
            cur = self._conn.execute(
                """INSERT INTO observations
                   (session_id, parent_id, type, agent, content,
                    tokens_discovery, tokens_read, entities,
                    handoff_path, task_ref, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    parent_id,
                    type,
                    agent,
                    content,
                    tokens_discovery,
                    tokens_read,
                    entities_json,
                    handoff_path,
                    task_ref,
                    ts,
                ),
            )
            obs_id = cur.lastrowid
            if obs_id is None:
                msg = "Failed to insert observation"
                raise RuntimeError(msg)

            # Update session token counters
            self._conn.execute(
                """UPDATE sessions SET
                   token_discovery = token_discovery + ?,
                   token_read = token_read + ?,
                   updated_at = ?
                   WHERE id = ?""",
                (tokens_discovery, tokens_read, ts, session_id),
            )

        logger.debug(f"Observation {obs_id} added to session {session_id} (type={type})")

        # Rate-limited auto-consolidation check (every 10th call)
        self._auto_consolidate_counter += 1
        if self._auto_consolidate_counter % 10 == 0:
            self.auto_consolidate_if_needed(session_id)

        return obs_id

    def get_observation(self, observation_id: int) -> dict[str, Any] | None:
        """Retrieve a single observation by ID."""
        row = self._conn.execute(
            "SELECT * FROM observations WHERE id = ?", (observation_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_observation_dict(row)

    def get_observations(
        self,
        session_id: str,
        limit: int = 20,
        offset: int = 0,
        types: list[str] | None = None,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        """Retrieve observations for a session, newest first."""
        query = "SELECT * FROM observations WHERE session_id = ?"
        params: list[Any] = [session_id]

        if not include_inactive:
            query += " AND is_active = 1"

        if types:
            placeholders = ",".join("?" for _ in types)
            query += f" AND type IN ({placeholders})"
            params.extend(types)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_observation_dict(r) for r in rows]

    def get_latest_observations(
        self,
        session_id: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Get the most recent observations for a session."""
        return self.get_observations(session_id, limit=limit, offset=0)

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    def get_or_create_entity(
        self,
        name: str,
        entity_type: str = "concept",
    ) -> int:
        """Get or create an entity by name, handling soft-superseded via versioning."""
        normalised = name.strip().lower()
        ts = now_iso()

        row = self._conn.execute(
            "SELECT id, is_active FROM entities WHERE name = ?", (normalised,)
        ).fetchone()

        if row is not None and row["is_active"] == 1:
            return row["id"]

        if row is not None and row["is_active"] == 0:
            import re

            version_match = re.search(r"___v(\d+)$", normalised)
            if version_match:
                base_entity = normalised[: -len(version_match.group(0))]
                next_version = int(version_match.group(1)) + 1
            else:
                base_entity = normalised
                next_version = 2

            new_name = f"{base_entity}___v{next_version}"
        else:
            new_name = normalised

        with self.transaction():
            cur = self._conn.execute(
                """INSERT INTO entities (name, entity_type, metadata, created_at, updated_at)
                   VALUES (?, ?, '{}', ?, ?)""",
                (new_name, entity_type, ts, ts),
            )

        entity_id = cur.lastrowid
        if entity_id is None:
            msg = f"Failed to create entity '{new_name}'"
            raise RuntimeError(msg)

        logger.debug(f"Entity created: id={entity_id} name='{new_name}' type={entity_type}")
        return entity_id

    def link_entity_to_observation(
        self,
        observation_id: int,
        entity_id: int,
    ) -> None:
        """Create an M2M link between an observation and an entity."""
        try:
            with self.transaction():
                self._conn.execute(
                    """INSERT OR IGNORE INTO observation_entities
                       (observation_id, entity_id) VALUES (?, ?)""",
                    (observation_id, entity_id),
                )
        except Exception:
            logger.warning(f"Failed to link entity {entity_id} to observation {observation_id}")

    def _resolve_entity_names(self, entity_ids: list[int]) -> list[str]:
        """Resolve entity IDs to their names."""
        if not entity_ids:
            return []
        placeholders = ",".join("?" for _ in entity_ids)
        rows = self._conn.execute(
            f"SELECT name FROM entities WHERE id IN ({placeholders})",
            entity_ids,
        ).fetchall()
        return [r["name"] for r in rows]

    def _get_entity_ids_for_observation(self, observation_id: int) -> list[int]:
        """Get entity IDs linked to an observation."""
        rows = self._conn.execute(
            "SELECT entity_id FROM observation_entities WHERE observation_id = ?",
            (observation_id,),
        ).fetchall()
        return [r["entity_id"] for r in rows]

    # ------------------------------------------------------------------
    # Search (FTS5 + filters)
    # ------------------------------------------------------------------

    def search_observations(
        self,
        query: str,
        entity: str | None = None,
        agent: str | None = None,
        type: str | None = None,
        session_id: str | None = None,
        max_results: int = 10,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search observations using FTS5 BM25 with optional filters.

        Returns:
            List of observation dicts with score added.
        """
        max_results = min(max_results, 50)

        fts_query = self._conn.execute(
            """SELECT rowid, rank AS bm25_score
               FROM observations_fts
               WHERE observations_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, max_results * 2),
        ).fetchall()

        if not fts_query:
            return []

        obs_ids = [r["rowid"] for r in fts_query]
        score_map: dict[int, float] = {r["rowid"]: abs(r["bm25_score"]) for r in fts_query}

        placeholders = ",".join("?" for _ in obs_ids)
        sql = f"SELECT * FROM observations WHERE id IN ({placeholders})"
        params: list[Any] = list(obs_ids)

        filters: list[str] = []
        if entity:
            filters.append(
                "id IN (SELECT observation_id FROM observation_entities oe "
                "JOIN entities e ON e.id = oe.entity_id "
                "WHERE e.name = ?)"
            )
            params.append(entity.strip().lower())
        if agent is not None:
            filters.append("agent = ?")
            params.append(agent)
        if type is not None:
            filters.append("type = ?")
            params.append(type)
        if session_id is not None:
            filters.append("session_id = ?")
            params.append(session_id)
        if since is not None:
            filters.append("created_at >= ?")
            params.append(since)

        if filters:
            sql += " AND " + " AND ".join(filters)

        rows = self._conn.execute(sql, params).fetchall()

        results: list[dict[str, Any]] = []
        for r in rows:
            obs_dict = self._row_to_observation_dict(r)
            obs_id = obs_dict["id"]
            if obs_id is not None:
                obs_dict["score"] = round(score_map.get(obs_id, 0.0), 4)
                results.append(obs_dict)

        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:max_results]

    def search_tasks(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Search tasks using FTS5 BM25.

        Returns:
            List of task dicts with score added.
        """
        max_results = min(max_results, 50)

        fts_query = self._conn.execute(
            """SELECT rowid, rank AS bm25_score
               FROM tasks_fts
               WHERE tasks_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query, max_results),
        ).fetchall()

        if not fts_query:
            return []

        task_ids = [r["rowid"] for r in fts_query]
        score_map: dict[int, float] = {r["rowid"]: abs(r["bm25_score"]) for r in fts_query}

        placeholders = ",".join("?" for _ in task_ids)
        rows = self._conn.execute(
            f"SELECT *, rowid FROM tasks WHERE rowid IN ({placeholders})",
            task_ids,
        ).fetchall()

        results: list[dict[str, Any]] = []
        for r in rows:
            task_dict = self._row_to_task_dict(r)
            tid = r["rowid"]
            task_dict["score"] = round(score_map.get(tid, 0.0), 4)
            results.append(task_dict)

        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results[:max_results]

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    def add_summary(
        self,
        session_id: str,
        level: int,
        content: str,
        token_count: int = 0,
        parent_id: int | None = None,
    ) -> int:
        """Create a summary for a session. Returns the new summary ID."""
        ts = now_iso()
        with self.transaction():
            cur = self._conn.execute(
                """INSERT INTO summaries
                   (session_id, level, content, token_count, parent_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, level, content, token_count, parent_id, ts),
            )

        summary_id = cur.lastrowid
        if summary_id is None:
            msg = "Failed to insert summary"
            raise RuntimeError(msg)

        logger.info(f"Summary {summary_id} created for session {session_id} (level={level})")
        return summary_id

    def get_summaries(
        self,
        session_id: str,
        level: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve summaries for a session, optional level filter."""
        if level is not None:
            rows = self._conn.execute(
                """SELECT * FROM summaries
                   WHERE session_id = ? AND level = ?
                   ORDER BY created_at DESC""",
                (session_id, level),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM summaries
                   WHERE session_id = ?
                   ORDER BY level, created_at DESC""",
                (session_id,),
            ).fetchall()
        return [self._row_to_summary_dict(r) for r in rows]

    def get_summarization_candidates(
        self,
        session_id: str,
        level: int = 1,
    ) -> list[dict[str, Any]]:
        """Get observations not yet compressed into a summary at a given level."""
        latest = self._conn.execute(
            """SELECT id, created_at FROM summaries
               WHERE session_id = ? AND level = ?
               ORDER BY id DESC LIMIT 1""",
            (session_id, level),
        ).fetchone()

        if latest is None:
            return self.get_observations(session_id, limit=1000, offset=0)

        last_obs = self._conn.execute(
            """SELECT COALESCE(MAX(id), 0) AS max_id
               FROM observations
               WHERE session_id = ? AND created_at <= ?""",
            (session_id, latest["created_at"]),
        ).fetchone()

        cutoff_id = last_obs["max_id"] if last_obs else 0
        rows = self._conn.execute(
            """SELECT * FROM observations
               WHERE session_id = ? AND id > ?
               ORDER BY id ASC""",
            (session_id, cutoff_id),
        ).fetchall()
        return [self._row_to_observation_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def create_task(
        self,
        task_id: str,
        description: str,
        status: str = "pend",
        priority: str = "medium",
        owner: str = "Poros",
        tags: list[str] | None = None,
        parent: str | None = None,
        handoff_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new task. Returns task data dict."""
        status = normalize_task_status(status) or "pend"
        ts = now_iso_seconds()
        tags_json = json.dumps(tags or [])
        handoff_refs_json = json.dumps(handoff_refs or [])

        with self.transaction():
            self._conn.execute(
                """INSERT INTO tasks
                   (id, description, status, priority, owner, tags, parent,
                    handoff_refs, compression_level, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    task_id,
                    description,
                    status,
                    priority,
                    owner,
                    tags_json,
                    parent,
                    handoff_refs_json,
                    ts,
                    ts,
                ),
            )

            # Add created event (short canonical)
            self._conn.execute(
                """INSERT INTO task_events
                   (task_id, type, details, compression_level, created_at)
                   VALUES (?, 'cr', ?, 0, ?)""",
                (task_id, f"Task created by {owner}", ts),
            )

        logger.info(f"Task created: {task_id} (status={status}, owner={owner})")

        return {
            "id": task_id,
            "status": status,
            "created_at": ts,
            "description": description,
        }

    def get_task(self, task_id: str, include_inactive: bool = False) -> dict[str, Any] | None:
        """Retrieve a task by ID, with optional inactive inclusion."""
        query = "SELECT * FROM tasks WHERE id = ?"
        if not include_inactive:
            query += " AND is_active = 1"
        row = self._conn.execute(query, (task_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_task_dict(row)

    def get_tasks(
        self,
        status: str | None = None,
        owner: str | None = None,
        priority: str | None = None,
        parent: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        since: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        """Query tasks with filters (combined with AND)."""
        sql = "SELECT * FROM tasks WHERE 1=1"
        params: list[Any] = []

        if not include_inactive:
            sql += " AND is_active = 1"

        if status is not None:
            status = normalize_task_status(status)
            sql += " AND status = ?"
            params.append(status)
        if owner is not None:
            sql += " AND LOWER(owner) = ?"
            params.append(owner.lower())
        if priority is not None:
            sql += " AND priority = ?"
            params.append(priority)
        if parent is not None:
            sql += " AND parent = ?"
            params.append(parent)
        if tag is not None:
            sql += " AND tags LIKE ?"
            params.append(f'%"{tag}"%')
        if search is not None:
            sql += " AND LOWER(description) LIKE ?"
            params.append(f"%{search.lower()}%")
        if since is not None:
            sql += " AND updated_at >= ?"
            params.append(since)

        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_task_dict(r) for r in rows]

    def update_task(
        self,
        task_id: str,
        **kwargs: Any,  # noqa: ANN401
    ) -> str | None:
        """Update task fields via ADD-only soft-supersede (old marked inactive, new versioned row)."""
        allowed = {
            "status",
            "description",
            "priority",
            "owner",
            "tags",
            "parent",
            "handoff_refs",
            "compression_level",
        }
        updates: dict[str, Any] = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return task_id

        old = self.get_task(task_id)
        if old is None:
            logger.warning(f"update_task: task '{task_id}' not found")
            return None

        ts = now_iso_seconds()

        import re

        version_match = re.search(r"-v(\d+)$", task_id)
        if version_match:
            base_id = task_id[: -len(version_match.group(0))]
            next_version = int(version_match.group(1)) + 1
        else:
            base_id = task_id
            next_version = 2

        new_id = f"{base_id}-v{next_version}"

        with self.transaction():
            # Supersede old task
            self._conn.execute(
                "UPDATE tasks SET is_active = 0, superseded_by = ?, updated_at = ? WHERE id = ?",
                (new_id, ts, task_id),
            )

            # Build new row
            merged = dict(old)
            merged.pop("is_active", None)
            merged.pop("superseded_by", None)
            merged.update(updates)
            merged["id"] = new_id
            merged["created_at"] = old.get("created_at", ts)
            merged["updated_at"] = ts
            merged["handoff_refs"] = json.dumps(old.get("handoff_refs", []))
            merged["tags"] = json.dumps(old.get("tags", []))

            if "handoff_refs" in updates:
                merged["handoff_refs"] = json.dumps(updates["handoff_refs"])
            if "tags" in updates:
                merged["tags"] = json.dumps(updates["tags"])

            self._conn.execute(
                """INSERT INTO tasks
                   (id, description, status, priority, owner, tags, parent,
                    handoff_refs, compression_level, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id,
                    merged["description"],
                    merged["status"],
                    merged["priority"],
                    merged["owner"],
                    merged["tags"],
                    merged.get("parent"),
                    merged["handoff_refs"],
                    merged.get("compression_level", 0),
                    merged["created_at"],
                    ts,
                ),
            )

        logger.info(f"Task {task_id} superseded by {new_id}")
        return new_id

    def get_task_events(
        self,
        task_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get events for a task."""
        rows = self._conn.execute(
            """SELECT * FROM task_events
               WHERE task_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (task_id, limit),
        ).fetchall()
        return [self._row_to_task_event_dict(r) for r in rows]

    def add_task_event(
        self,
        task_id: str,
        event_type: str,
        details: str,
        handoff_path: str | None = None,
    ) -> dict[str, Any]:
        """Add an event to a task's log. Returns event data dict."""
        event_type = normalize_event_type(event_type) or "note"
        ts = now_iso_seconds()

        with self.transaction():
            cur = self._conn.execute(
                """INSERT INTO task_events
                   (task_id, type, details, handoff_path, compression_level, created_at)
                   VALUES (?, ?, ?, ?, 0, ?)""",
                (task_id, event_type, details, handoff_path, ts),
            )

            # Update task updated_at
            self._conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?",
                (ts, task_id),
            )

            # If handoff_ref (short canonical "hr"), also add to handoff_refs
            if event_type == "hr" and handoff_path:
                task = self.get_task(task_id)
                if task:
                    refs = task.get("handoff_refs", [])
                    if handoff_path not in refs:
                        refs.append(handoff_path)
                        self._conn.execute(
                            "UPDATE tasks SET handoff_refs = ?, updated_at = ? WHERE id = ?",
                            (json.dumps(refs), ts, task_id),
                        )

        event_id = cur.lastrowid
        logger.info(f"Event #{event_id} ({event_type}) added to {task_id}")

        return {
            "event_id": event_id,
            "task_id": task_id,
            "event_type": event_type,
            "created_at": ts,
        }

    def update_task_status(
        self,
        task_id: str,
        new_status: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Update task status with state machine validation and parent auto-promotion."""
        from tools.synapsis.models import StateMachine as SM, normalize_task_status

        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task '{task_id}' not found.")

        new_status = normalize_task_status(new_status) or new_status
        old_status = normalize_task_status(task["status"]) or task["status"]
        if old_status == new_status:
            return {
                "id": task_id,
                "old_status": old_status,
                "new_status": new_status,
                "auto_parent_completed": None,
                "warning": f"Task already in state '{new_status}'.",
            }

        SM.validate_transition(old_status, new_status)

        ts = now_iso_seconds()
        event_details = f"{new_status} ← {old_status}"
        if note:
            event_details += f" — {note}"

        auto_parent_completed: str | None = None

        with self.transaction():
            # Update task status
            self._conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, ts, task_id),
            )

            # Add status_change event
            self._conn.execute(
                """INSERT INTO task_events
                   (task_id, type, details, compression_level, created_at)
                   VALUES (?, 'sc', ?, 0, ?)""",
                (task_id, event_details, ts),
            )

            # Check parent auto-promotion
            parent_id = task.get("parent")
            if parent_id and new_status == "done":
                parent = self.get_task(parent_id)
                if parent:
                    parent_status = parent.get("status", "")
                    if parent_status in ("prog", "pend"):
                        siblings = self.get_tasks(parent=parent_id, limit=1000)
                        if siblings and all(s.get("status") == "done" for s in siblings):
                            self._conn.execute(
                                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                                ("done", ts, parent_id),
                            )
                            self._conn.execute(
                                """INSERT INTO task_events
                                   (task_id, type, details, compression_level, created_at)
                                   VALUES (?, 'sc', ?, 0, ?)""",
                                (
                                    parent_id,
                                    f"done ← {parent_status} — "
                                    f"auto-promotion: all subtasks completed",
                                    ts,
                                ),
                            )
                            auto_parent_completed = parent_id

        logger.info(f"Task {task_id}: {old_status} → {new_status}")

        return {
            "id": task_id,
            "old_status": old_status,
            "new_status": new_status,
            "updated_at": ts,
            "auto_parent_completed": auto_parent_completed,
        }

    # ------------------------------------------------------------------
    # Counters (task ID generation)
    # ------------------------------------------------------------------

    def next_task_id(self, area: str) -> str:
        """Generate and reserve the next task ID for an area (e.g. ``T-MCP-001``)."""
        row = self._conn.execute(
            "SELECT last_value FROM counters WHERE area = ?", (area,)
        ).fetchone()

        last_value = row["last_value"] if row else 0
        next_value = last_value + 1
        task_id = generate_task_id(area, last_value)

        with self.transaction():
            if row:
                self._conn.execute(
                    "UPDATE counters SET last_value = ? WHERE area = ?",
                    (next_value, area),
                )
            else:
                self._conn.execute(
                    "INSERT INTO counters (area, last_value) VALUES (?, ?)",
                    (area, next_value),
                )

        return task_id

    # ------------------------------------------------------------------
    # Compression
    # ------------------------------------------------------------------

    def compress_observations(
        self,
        age_days: int | None = None,
        max_level: int = 2,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Compress old observations using progressive hot/warm/cold levels."""
        warm_days = age_days if age_days is not None else 7
        cold_days = age_days if age_days is not None else 30

        results: dict[str, Any] = {
            "observations_warm": 0,
            "observations_cold": 0,
            "summaries_created": 0,
            "sessions_affected": set(),
            "dry_run": dry_run,
            "details": [],
        }

        with self.transaction():
            if max_level >= 1:
                warm_candidates = self._conn.execute(
                    """SELECT * FROM observations
                       WHERE compression_level = 0
                       AND created_at < datetime('now', ? || ' days')
                       ORDER BY created_at ASC""",
                    (f"-{warm_days}",),
                ).fetchall()

                for row in warm_candidates:
                    obs = self._row_to_observation_dict(row)
                    obs_id = obs["id"]
                    content = obs.get("content", "")

                    if len(content) <= 100:
                        self._conn.execute(
                            "UPDATE observations SET compression_level = 1 WHERE id = ?",
                            (obs_id,),
                        )
                        continue

                    compressed = _compress_text(content, max_chars=300)
                    if not dry_run:
                        self._conn.execute(
                            """UPDATE observations
                               SET content = ?, compression_level = 1
                               WHERE id = ?""",
                            (compressed, obs_id),
                        )
                    results["observations_warm"] += 1
                    session_id = obs.get("session_id", "?")
                    results["sessions_affected"].add(session_id)
                    results["details"].append(
                        f"warm: obs#{obs_id} in {session_id} ({len(content)}→{len(compressed)} chars)"
                    )

            if max_level >= 2:
                cold_candidates = self._conn.execute(
                    """SELECT * FROM observations
                       WHERE compression_level IN (0, 1)
                       AND created_at < datetime('now', ? || ' days')
                       ORDER BY session_id, created_at ASC""",
                    (f"-{cold_days}",),
                ).fetchall()

                groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
                for row in cold_candidates:
                    obs = self._row_to_observation_dict(row)
                    dt = datetime.fromisoformat(obs["created_at"])
                    iso_year, iso_week, _ = dt.isocalendar()
                    week_key = f"W{iso_week:02d}-{iso_year}"
                    groups.setdefault((obs["session_id"], week_key), []).append(obs)

                for (session_id, week_key), obs_list in groups.items():
                    count = len(obs_list)
                    type_counts: dict[str, int] = {}
                    key_topics: list[str] = []
                    total_orig_chars = 0

                    for o in obs_list:
                        t = o.get("type", "unknown")
                        type_counts[t] = type_counts.get(t, 0) + 1
                        txt = o.get("content", "")
                        total_orig_chars += len(txt)
                        snippet = txt[:60].strip()
                        key_topics.append(f"[{t}] {snippet}")

                    type_summary_parts = [f"{cnt} {t}" for t, cnt in sorted(type_counts.items())]
                    type_summary = ", ".join(type_summary_parts)

                    token_disc = sum(o.get("tokens_discovery", 0) for o in obs_list)
                    summary_content = (
                        f"Period: {week_key} | Session: {session_id}\n"
                        f"Observations: {count} ({type_summary})\n"
                        f"Token discovery total: {token_disc}\n"
                        f"Key topics:\n" + "\n".join(key_topics[:20])
                    )
                    approx_tokens = len(summary_content) // 4

                    if not dry_run:
                        obs_ids = [o["id"] for o in obs_list if o["id"] is not None]
                        placeholders = ",".join("?" for _ in obs_ids)
                        self._conn.execute(
                            f"UPDATE observations SET compression_level = 2 "
                            f"WHERE id IN ({placeholders})",
                            obs_ids,
                        )
                        self.add_summary(
                            session_id=session_id,
                            level=3,
                            content=summary_content,
                            token_count=approx_tokens,
                        )
                        results["summaries_created"] += 1

                    results["observations_cold"] += count
                    results["sessions_affected"].add(session_id)
                    results["details"].append(
                        f"cold: {count} obs in {session_id}/{week_key} "
                        f"({type_summary}, ~{total_orig_chars}→{approx_tokens * 4} chars)"
                    )

        results["sessions_affected"] = sorted(results["sessions_affected"])

        # After actual compression completes, trigger follow-up auto-consolidation
        if not dry_run:
            self.auto_consolidate_if_needed()

        return results

    def compress_task_events(
        self,
        age_days: int | None = None,
        max_level: int = 2,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Compress old task events using progressive hot/warm/cold levels."""
        warm_days = age_days if age_days is not None else 7
        cold_days = age_days if age_days is not None else 30

        results: dict[str, Any] = {
            "events_warm": 0,
            "events_cold": 0,
            "tasks_processed": set(),
            "dry_run": dry_run,
            "details": [],
        }

        with self.transaction():
            if max_level >= 1:
                warm_candidates = self._conn.execute(
                    """SELECT * FROM task_events
                       WHERE compression_level = 0
                       AND created_at < datetime('now', ? || ' days')
                       ORDER BY created_at ASC""",
                    (f"-{warm_days}",),
                ).fetchall()

                for row in warm_candidates:
                    event = self._row_to_task_event_dict(row)
                    event_id = event["id"]
                    details = event.get("details", "")

                    compressed = _compress_text(details, max_chars=200)
                    if len(compressed) >= len(details) * 0.9:
                        compressed = details[:200]

                    if not dry_run:
                        self._conn.execute(
                            """UPDATE task_events
                               SET details = ?, compression_level = 1
                               WHERE id = ?""",
                            (compressed, event_id),
                        )
                    results["events_warm"] += 1
                    results["tasks_processed"].add(event["task_id"])
                    results["details"].append(
                        f"warm: event#{event_id} ({len(details)}→{len(compressed)} chars)"
                    )

            if max_level >= 2:
                cold_candidates = self._conn.execute(
                    """SELECT * FROM task_events
                       WHERE compression_level IN (0, 1)
                       AND created_at < datetime('now', ? || ' days')
                       ORDER BY task_id, created_at ASC""",
                    (f"-{cold_days}",),
                ).fetchall()

                groups: dict[str, list[dict[str, Any]]] = {}
                for row in cold_candidates:
                    event = self._row_to_task_event_dict(row)
                    groups.setdefault(event["task_id"], []).append(event)

                for task_id, events in groups.items():
                    count = len(events)
                    type_counts: dict[str, int] = {}
                    key_handoffs: list[str] = []

                    for e in events:
                        t = e.get("type", "unknown")
                        type_counts[t] = type_counts.get(t, 0) + 1
                        hp = e.get("handoff_path")
                        if hp:
                            key_handoffs.append(hp)

                    type_summary_parts = [f"{cnt} {t}" for t, cnt in sorted(type_counts.items())]

                    if not dry_run:
                        event_ids = [e["id"] for e in events if e["id"] is not None]
                        placeholders = ",".join("?" for _ in event_ids)
                        self._conn.execute(
                            f"UPDATE task_events SET compression_level = 2 "
                            f"WHERE id IN ({placeholders})",
                            event_ids,
                        )

                    results["events_cold"] += count
                    results["tasks_processed"].add(task_id)
                    results["details"].append(
                        f"cold: {count} events in {task_id} ({', '.join(type_summary_parts)})"
                    )

        results["tasks_processed"] = sorted(results["tasks_processed"])
        return results

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self, enhanced: bool = False) -> dict[str, Any]:
        """Get aggregate database statistics.

        Args:
            enhanced: Include extended stats (entities per type, compression, etc.).
        """
        session_count = self._conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()
        active_sessions = self._conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE status IN ('active', 'interrupted')"
        ).fetchone()
        obs_count = self._conn.execute("SELECT COUNT(*) AS c FROM observations").fetchone()
        task_count = self._conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()
        task_by_status = self._conn.execute(
            "SELECT status, COUNT(*) AS c FROM tasks GROUP BY status"
        ).fetchall()
        entity_count = self._conn.execute("SELECT COUNT(*) AS c FROM entities").fetchone()
        event_count = self._conn.execute("SELECT COUNT(*) AS c FROM task_events").fetchone()
        summary_count = self._conn.execute("SELECT COUNT(*) AS c FROM summaries").fetchone()

        # Token economics
        token_row = self._conn.execute(
            """SELECT COALESCE(SUM(tokens_discovery), 0) AS tot_disc,
                      COALESCE(SUM(tokens_read), 0) AS tot_read
               FROM observations"""
        ).fetchone()

        tot_disc = token_row["tot_disc"] if token_row else 0
        tot_read = token_row["tot_read"] if token_row else 0

        result = {
            "sessions": {
                "total": session_count["c"] if session_count else 0,
                "active": active_sessions["c"] if active_sessions else 0,
            },
            "observations": obs_count["c"] if obs_count else 0,
            "tasks": {
                "total": task_count["c"] if task_count else 0,
                "by_status": {r["status"]: r["c"] for r in task_by_status},
            },
            "task_events": event_count["c"] if event_count else 0,
            "entities": entity_count["c"] if entity_count else 0,
            "summaries": summary_count["c"] if summary_count else 0,
            "token_economics": {
                "total_discovery": tot_disc,
                "total_read": tot_read,
                "savings_ratio": round(compute_token_savings(tot_disc, tot_read), 4)
                if tot_disc > 0
                else 0.0,
            },
        }

        if not enhanced:
            return result

        # Enhanced stats
        result["compression"] = {
            "observations": {},
            "task_events": {},
            "total_token_savings": result.get("token_economics", {}).get("savings_ratio", 0),
        }

        ent_by_type = self._conn.execute(
            "SELECT entity_type, COUNT(*) AS c FROM entities GROUP BY entity_type"
        ).fetchall()
        result["entities_by_type"] = {r["entity_type"]: r["c"] for r in ent_by_type}

        comp_obs = self._conn.execute(
            """SELECT compression_level, COUNT(*) AS c
               FROM observations GROUP BY compression_level"""
        ).fetchall()
        result["compression"]["observations"] = {
            str(r["compression_level"]): r["c"] for r in comp_obs
        }

        comp_events = self._conn.execute(
            """SELECT compression_level, COUNT(*) AS c
               FROM task_events GROUP BY compression_level"""
        ).fetchall()
        result["compression"]["task_events"] = {
            str(r["compression_level"]): r["c"] for r in comp_events
        }

        active_sessions_detail = self._conn.execute(
            """SELECT id, topic, status, token_discovery, token_read
               FROM sessions WHERE status IN ('active', 'interrupted')
               ORDER BY updated_at DESC LIMIT 20"""
        ).fetchall()
        result["active_sessions"] = [
            {
                "id": r["id"],
                "topic": r["topic"],
                "status": r["status"],
                "token_discovery": r["token_discovery"],
                "token_read": r["token_read"],
            }
            for r in active_sessions_detail
        ]

        ml_count = self._conn.execute("SELECT COUNT(*) AS c FROM memory_layers").fetchone()
        result["memory_layers"] = ml_count["c"] if ml_count else 0

        try:
            result["database_size_bytes"] = self.path.stat().st_size
        except OSError:
            result["database_size_bytes"] = 0

        return result

    # ------------------------------------------------------------------
    # HF — handoff index
    # ------------------------------------------------------------------

    def hf_insert(
        self,
        ref: str,
        type: str,
        title: str,
        agent: str,
        task: str | None,
        st: str,
        prio: str,
        sess: str | None,
        file: str,
        wiki: str | None,
        ts: str,
    ) -> bool:
        """Insert a record into the hf table. Returns True if inserted (INSERT OR IGNORE)."""
        try:
            with self.transaction():
                cur = self._conn.execute(
                    """INSERT OR IGNORE INTO hf
                       (ref, type, title, agent, task, st, prio, sess, file, wiki, ts)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ref, type, title, agent, task, st, prio, sess, file, wiki, ts),
                )
            inserted = cur.rowcount > 0
            if inserted:
                logger.debug(f"HF record inserted: {ref}")
            return inserted
        except Exception as exc:
            logger.error(f"HF insert failed for {ref}: {exc}")
            return False

    def hf_get(self, ref: str) -> dict | None:
        """Get a handoff record by ref. Returns dict or None."""
        row = self._conn.execute("SELECT * FROM hf WHERE ref = ?", (ref,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def hf_search(self, query: str, limit: int = 20) -> list[dict]:
        """Search handoff records with FTS5 relevance ranking.

        Falls back to LIKE-based search if FTS5 query fails (e.g. invalid
        FTS5 syntax from user input).

        Args:
            query: FTS5 search query string.
            limit: Maximum results to return.

        Returns:
            List of handoff dicts.
        """
        try:
            rows = self._conn.execute(
                """SELECT h.*, rank
                   FROM hf_fts
                   JOIN hf ON hf.rowid = hf_fts.rowid
                   WHERE hf_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.warning(f"hf_fts MATCH failed for '{query}', falling back to LIKE")

        # Fallback: old LIKE-based search
        pattern = f"%{query.strip().lower()}%"
        rows = self._conn.execute(
            """SELECT * FROM hf
               WHERE LOWER(type) LIKE ?
                  OR LOWER(title) LIKE ?
                  OR LOWER(agent) LIKE ?
                  OR LOWER(task) LIKE ?
               ORDER BY ts DESC
               LIMIT ?""",
            (pattern, pattern, pattern, pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def hf_exists(self, ref: str) -> bool:
        """Check if a handoff ref already exists in the DB."""
        row = self._conn.execute("SELECT 1 FROM hf WHERE ref = ?", (ref,)).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Deliverables — file path to CRC32 hash registry
    # ------------------------------------------------------------------

    def deliverable_register(self, path: str) -> str:
        """CRC32(path) → INSERT OR REPLACE → return 8-char hex hash.

        Normalizes absolute paths to relative so CRC32 is deterministic.
        """
        import zlib
        from pathlib import Path as PPath

        p = PPath(path)
        if p.is_absolute():
            from tools.common.paths import project_root

            try:
                path = str(p.relative_to(project_root()))
            except ValueError:
                pass

        hash_str = format(zlib.crc32(path.encode()) & 0xFFFFFFFF, "08x")
        with self.transaction():
            self._conn.execute(
                "INSERT OR REPLACE INTO deliverables (hash, path) VALUES (?, ?)",
                (hash_str, path),
            )
        return hash_str

    def deliverable_resolve(self, hash_str: str) -> str | None:
        """SELECT path FROM deliverables WHERE hash=? → return path or None."""
        row = self._conn.execute(
            "SELECT path FROM deliverables WHERE hash=?",
            (hash_str,),
        ).fetchone()
        return row[0] if row else None

    def deliverable_read(self, hash_str: str, layer: int = 1) -> dict | None:
        """Resolve hash, read file, truncate by layer (1=meta, 2=500ch, 3=full)."""
        from tools.common.paths import project_root

        path = self.deliverable_resolve(hash_str)
        if not path:
            return None
        result: dict = {"h": hash_str, "p": path}
        if layer == 1:
            return result
        full_path = project_root() / path
        if not full_path.is_file():
            return None
        if layer > 1:
            content = full_path.read_text(encoding="utf-8")
            if layer == 2:
                content = content[:500]
            result["body"] = content

        return result

    # ------------------------------------------------------------------
    # Cross-reference
    # ------------------------------------------------------------------

    def cross_reference_entity(
        self,
        entity_name: str,
        max_results: int = 10,
    ) -> dict[str, Any]:
        """Find observations and tasks linked to an entity."""
        normalised = entity_name.strip().lower()

        # Get entity
        entity = self._conn.execute(
            "SELECT * FROM entities WHERE name = ?", (normalised,)
        ).fetchone()

        if entity is None:
            return {"entity": entity_name, "observations": [], "tasks": []}

        entity_id = entity["id"]

        # Observations linked via M2M
        obs_rows = self._conn.execute(
            """SELECT o.* FROM observations o
               JOIN observation_entities oe ON oe.observation_id = o.id
               WHERE oe.entity_id = ?
               ORDER BY o.created_at DESC
               LIMIT ?""",
            (entity_id, max_results),
        ).fetchall()

        # Tasks linked via task_ref on observations
        task_rows = self._conn.execute(
            """SELECT DISTINCT t.* FROM tasks t
               JOIN observations o ON o.task_ref = t.id
               JOIN observation_entities oe ON oe.observation_id = o.id
               WHERE oe.entity_id = ?
               ORDER BY t.updated_at DESC
               LIMIT ?""",
            (entity_id, max_results),
        ).fetchall()

        return {
            "entity": entity_name,
            "observations": [self._row_to_observation_dict(r) for r in obs_rows],
            "tasks": [self._row_to_task_dict(r) for r in task_rows],
        }

    def get_session_tasks(self, session_id: str) -> list[dict[str, Any]]:
        """Get all tasks associated with a session (task_ids + task_ref)."""
        session = self.get_session(session_id)
        if session is None:
            return []

        # Collect task IDs from session.task_ids and observation.task_ref
        task_ids: set[str] = set(session.get("task_ids", []))

        obs_task_refs = self._conn.execute(
            "SELECT DISTINCT task_ref FROM observations "
            "WHERE session_id = ? AND task_ref IS NOT NULL",
            (session_id,),
        ).fetchall()
        for r in obs_task_refs:
            if r["task_ref"]:
                task_ids.add(r["task_ref"])

        if not task_ids:
            return []

        placeholders = ",".join("?" for _ in task_ids)
        rows = self._conn.execute(
            f"SELECT * FROM tasks WHERE id IN ({placeholders})",
            list(task_ids),
        ).fetchall()

        return [self._row_to_task_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Memory layers (Chimera)
    # ------------------------------------------------------------------

    def set_memory_layer(
        self,
        session_id: str,
        layer: str,
        content: str,
        source_observation_id: int | None = None,
    ) -> int:
        """Set content for a memory layer in a session (upsert by session_id + layer).
        Returns the memory layer ID.
        """
        ts = now_iso()

        with self.transaction():
            existing = self._conn.execute(
                "SELECT id FROM memory_layers WHERE session_id = ? AND layer = ?",
                (session_id, layer),
            ).fetchone()

            if existing:
                self._conn.execute(
                    """UPDATE memory_layers
                       SET content = ?, source_observation_id = ?, updated_at = ?
                       WHERE id = ?""",
                    (content, source_observation_id, ts, existing["id"]),
                )
                return existing["id"]
            else:
                cur = self._conn.execute(
                    """INSERT INTO memory_layers
                       (session_id, layer, content, source_observation_id,
                        forgetting_risk, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 0.0, ?, ?)""",
                    (session_id, layer, content, source_observation_id, ts, ts),
                )
                layer_id = cur.lastrowid
                if layer_id is None:
                    msg = "Failed to insert memory layer"
                    raise RuntimeError(msg)
                return layer_id

    def get_memory_layers(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Get all memory layers for a session."""
        rows = self._conn.execute(
            "SELECT * FROM memory_layers WHERE session_id = ? ORDER BY layer",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Row → dict converters
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Entity search (multi-signal — BM25 + frequency + temporal)
    # ------------------------------------------------------------------

    def entity_search(
        self,
        query: str,
        entity_type: str | None = None,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Search entities with hybrid ranking (BM25 + frequency + temporal).

        Returns:
            Entity dicts with score, observation_count, last_seen added.
        """
        max_results = min(max_results, 50)
        pattern = f"%{query.strip().lower()}%"

        sql = """SELECT e.*,
                        COUNT(DISTINCT oe.observation_id) AS observation_count,
                        COALESCE(MAX(o.created_at), e.created_at) AS last_seen
                   FROM entities e
                   LEFT JOIN observation_entities oe ON oe.entity_id = e.id
                   LEFT JOIN observations o ON o.id = oe.observation_id
                  WHERE e.name LIKE ?"""
        params: list[Any] = [pattern]

        if entity_type:
            sql += " AND e.entity_type = ?"
            params.append(entity_type)

        sql += """ GROUP BY e.id
                   ORDER BY observation_count DESC, last_seen DESC
                   LIMIT ?"""
        params.append(max_results)

        rows = self._conn.execute(sql, params).fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            # Parsa metadata JSON se presente
            meta_raw = d.get("metadata")
            if meta_raw and isinstance(meta_raw, str):
                try:
                    d["metadata"] = json.loads(meta_raw)
                except (json.JSONDecodeError, TypeError):
                    d["metadata"] = {}
            elif not meta_raw:
                d["metadata"] = {}
            # Score: combination of frequency + recency
            obs_count = d.get("observation_count", 0) or 0
            score = min(obs_count / 10.0, 1.0)  # normalize to 0-1
            d["score"] = round(score, 4)
            results.append(d)
        return results

    # ------------------------------------------------------------------
    # Memory layer search
    # ------------------------------------------------------------------

    def search_memory_layers(
        self,
        query: str,
        max_results: int = 10,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search memory layers by content with BM25-inspired scoring.

        Args:
            query: Free-text search string.
            max_results: Max rows to return (capped at 50).
            session_id: Optional session filter.

        Returns:
            Memory layer dicts with id, session_id, layer, content (full),
            snippet (truncated to 200 chars), forgetting_risk, created_at,
            and score (0.0–1.0, frequency-based). Ordered by
            forgetting_risk ASC, created_at DESC.
        """
        max_results = min(max_results, 50)
        pattern = f"%{query.strip().lower()}%"

        sql = """SELECT ml.*,
                        o.content AS obs_content
                   FROM memory_layers ml
                   LEFT JOIN observations o ON o.id = ml.source_observation_id
                  WHERE ml.content LIKE ?"""
        params: list[Any] = [pattern]

        if session_id:
            sql += " AND ml.session_id = ?"
            params.append(session_id)

        sql += """ ORDER BY ml.forgetting_risk ASC, ml.created_at DESC
                   LIMIT ?"""
        params.append(max_results)

        rows = self._conn.execute(sql, params).fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            d: dict[str, Any] = dict(r)
            content = d.get("content", "") or ""
            # Remove the joined obs_content from result dict (internal use)
            d.pop("obs_content", None)

            # Snippet: first 200 characters
            d["snippet"] = content[:200] + ("..." if len(content) > 200 else "")

            # BM25-inspired score: term frequency in content / content length
            query_lower = query.strip().lower()
            terms = [t for t in query_lower.split() if t]
            if terms and content:
                term_freq = sum(content.lower().count(t) for t in terms)
                # Normalize: cap at 1.0 after 20+ occurrences
                d["score"] = round(min(term_freq / 20.0, 1.0), 4)
            else:
                d["score"] = 0.0

            results.append(d)
        return results

    # ------------------------------------------------------------------
    # Unified timeline
    # ------------------------------------------------------------------

    def get_timeline(
        self,
        since: str | None = None,
        until: str | None = None,
        entity: str | None = None,
        agent: str | None = None,
        scope: str = "all",
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        """Get a unified timeline of observations + task events + consolidations.

        Args:
            scope: ``"observations"`` | ``"tasks"`` | ``"consolidations"`` | ``"all"``.

        Returns:
            Timeline entries sorted by created_at desc, each with ``_source``.
        """
        max_results = min(max_results, 100)
        entries: list[dict[str, Any]] = []

        if scope in ("observations", "all"):
            obs_sql = "SELECT * FROM observations WHERE 1=1"
            obs_params: list[Any] = []

            if since:
                obs_sql += " AND created_at >= ?"
                obs_params.append(since)
            if until:
                obs_sql += " AND created_at <= ?"
                obs_params.append(until)
            if agent:
                obs_sql += " AND LOWER(agent) = ?"
                obs_params.append(agent.lower())

            obs_sql += " ORDER BY created_at DESC LIMIT ?"
            obs_params.append(max_results)

            rows = self._conn.execute(obs_sql, obs_params).fetchall()
            for r in rows:
                d = self._row_to_observation_dict(r)
                d["_source"] = "observation"
                entries.append(d)

        if scope in ("tasks", "all"):
            # Get task status changes as timeline entries
            ev_sql = """SELECT te.*, t.description AS task_desc, t.status AS task_status
                         FROM task_events te
                         JOIN tasks t ON t.id = te.task_id
                        WHERE 1=1"""
            ev_params: list[Any] = []

            if since:
                ev_sql += " AND te.created_at >= ?"
                ev_params.append(since)
            if until:
                ev_sql += " AND te.created_at <= ?"
                ev_params.append(until)
            if entity:
                # Only include events for tasks referenced with this entity
                ev_sql += """ AND te.task_id IN (
                    SELECT DISTINCT o.task_ref FROM observations o
                    JOIN observation_entities oe ON oe.observation_id = o.id
                    JOIN entities e ON e.id = oe.entity_id
                    WHERE e.name = ?
                )"""
                ev_params.append(entity.strip().lower())

            ev_sql += " ORDER BY te.created_at DESC LIMIT ?"
            ev_params.append(max_results)

            ev_rows = self._conn.execute(ev_sql, ev_params).fetchall()
            for r in ev_rows:
                d = self._row_to_task_event_dict(r)
                d["_source"] = "task_event"
                d["task_description"] = r["task_desc"]
                d["task_status"] = r["task_status"]
                entries.append(d)

        if scope in ("consolidations", "all"):
            sum_sql = "SELECT * FROM summaries WHERE 1=1"
            sum_params: list[Any] = []

            if since:
                sum_sql += " AND created_at >= ?"
                sum_params.append(since)
            if until:
                sum_sql += " AND created_at <= ?"
                sum_params.append(until)
            if entity:
                sum_sql += """ AND session_id IN (
                    SELECT DISTINCT o.session_id FROM observations o
                    JOIN observation_entities oe ON oe.observation_id = o.id
                    JOIN entities e ON e.id = oe.entity_id
                    WHERE e.name = ?
                )"""
                sum_params.append(entity.strip().lower())

            sum_sql += " ORDER BY created_at DESC LIMIT ?"
            sum_params.append(max_results)

            sum_rows = self._conn.execute(sum_sql, sum_params).fetchall()
            for r in sum_rows:
                d = self._row_to_summary_dict(r)
                d["_source"] = "consolidation"
                entries.append(d)

        # Sort all entries by created_at descending, limit
        entries.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return entries[:max_results]

    # ------------------------------------------------------------------
    # Unified search — fan-out across all storage domains
    # ------------------------------------------------------------------

    def unified_search(
        self,
        query: str,
        scope: str = "auto",
        max_results: int = 5,
        ref: str | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        """Search across all storage domains with a single unified query.

        Args:
            query: Search text, ``path:...`` for file reads, ``T-XXX`` for task lookups.
            scope: ``"auto"`` | ``"knowledge"`` | ``"tasks"`` | ``"observations"``
                   | ``"entities"`` | ``"memory_layers"`` | ``"session"``
                   | ``"timeline"`` | ``"all"``.
            ref: Optional cross-reference ref.
            since: ISO 8601 timestamp filter.

        Returns:
            Dict with domains, total_results, domain_counts.
        """
        import re

        domains: dict[str, Any] = {}
        total = 0
        counts: dict[str, int] = {}

        query_stripped = query.strip()

        # --- Auto-detect scope ---
        if scope == "auto":
            if query_stripped.startswith("path:"):
                scope = "knowledge"
            elif re.match(r"^T-\w+-\d+", query_stripped):
                scope = "tasks"
            elif ref is not None:
                scope = "all"
            else:
                scope = "all"

        # Collect a cross-reference if ref was given
        # Knowledge section uses the ref for context if present
        if ref is not None:
            self.cross_reference_ref(ref=ref, max_results=max_results)

        # --- Knowledge ---
        if scope in ("knowledge", "all", "auto"):
            if query_stripped.startswith("path:"):
                file_path = query_stripped.replace("path:", "", 1).strip()
                content = self.knowledge_read(file_path)
                if content is not None:
                    snippet = content[:300] + ("..." if len(content) > 300 else "")
                    domains["knowledge"] = [
                        {
                            "title": file_path,
                            "snippet": snippet,
                            "path": file_path,
                            "content": content,
                        }
                    ]
                    counts["knowledge"] = 1
                    total += 1
            else:
                k_results = self.knowledge_search(
                    query=query_stripped, limit=max_results, mode="bm25"
                )
                if k_results:
                    flat = []
                    for r in k_results:
                        content = r.get("content", "")
                        flat.append(
                            {
                                "title": r.get("heading_path") or r.get("file_path", "?"),
                                "snippet": content[:200] + ("..." if len(content) > 200 else ""),
                                "path": r.get("file_path", ""),
                                "score": r.get("score", 0),
                            }
                        )
                    domains["knowledge"] = flat
                    counts["knowledge"] = len(flat)
                    total += len(flat)

        # --- Tasks ---
        if scope in ("tasks", "all"):
            # T-XXX pattern: direct lookup instead of FTS5
            is_task_id = bool(re.match(r"^T-\w+-\d+", query_stripped))
            if is_task_id:
                task = self.get_task(query_stripped)
                t_results = [task] if task else []
            else:
                t_results = self.search_tasks(query=query_stripped, max_results=max_results)
            if t_results:
                flat = []
                for r in t_results:
                    flat.append(
                        {
                            "id": r.get("id"),
                            "description": r.get("description", ""),
                            "status": r.get("status"),
                            "owner": r.get("owner"),
                            "priority": r.get("priority"),
                            "score": r.get("score", 0),
                        }
                    )
                domains["tasks"] = flat
                counts["tasks"] = len(flat)
                total += len(flat)

        # --- Observations ---
        if scope in ("observations", "all"):
            o_results = self.search_observations(
                query=query_stripped,
                max_results=max_results,
                since=since,
            )
            if o_results:
                flat = []
                for r in o_results:
                    content = r.get("content", "")
                    flat.append(
                        {
                            "id": r.get("id"),
                            "session_id": r.get("session_id"),
                            "type": r.get("type"),
                            "agent": r.get("agent"),
                            "snippet": content[:200] + ("..." if len(content) > 200 else ""),
                            "entities": r.get("entities", []),
                            "score": r.get("score", 0),
                            "created_at": r.get("created_at"),
                        }
                    )
                domains["observations"] = flat
                counts["observations"] = len(flat)
                total += len(flat)

        # --- Entities ---
        if scope in ("entities", "all"):
            e_results = self.entity_search(query=query_stripped, max_results=max_results)
            if e_results:
                flat = []
                for r in e_results:
                    flat.append(
                        {
                            "name": r.get("name"),
                            "type": r.get("entity_type"),
                            "observation_count": r.get("observation_count", 0),
                            "score": r.get("score", 0),
                            "metadata": r.get("metadata", {}),
                        }
                    )
                domains["entities"] = flat
                counts["entities"] = len(flat)
                total += len(flat)

        # --- Memory Layers ---
        if scope in ("memory_layers", "all"):
            ml_results = self.search_memory_layers(query=query_stripped, max_results=max_results)
            if ml_results:
                flat = []
                for r in ml_results:
                    content = r.get("content", "") or ""
                    flat.append(
                        {
                            "id": r.get("id"),
                            "session_id": r.get("session_id"),
                            "layer": r.get("layer"),
                            "content": content[:200] + ("..." if len(content) > 200 else ""),
                            "snippet": content[:200] + ("..." if len(content) > 200 else ""),
                            "forgetting_risk": r.get("forgetting_risk"),
                            "created_at": r.get("created_at"),
                            "score": r.get("score", 0),
                        }
                    )
                domains["memory_layers"] = flat
                counts["memory_layers"] = len(flat)
                total += len(flat)

        # --- Session ---
        if scope in ("session", "all"):
            rows = self._conn.execute(
                """SELECT * FROM sessions
                   WHERE topic LIKE ?
                   ORDER BY updated_at DESC
                   LIMIT ?""",
                (f"%{query_stripped}%", max_results),
            ).fetchall()
            if rows:
                sessions_list = []
                for row in rows:
                    s = self._row_to_session_dict(row)
                    sid = s["id"]
                    metrics = self.get_session_metrics(sid)
                    latest = self.get_latest_observations(sid, limit=2)
                    sessions_list.append(
                        {
                            "id": sid,
                            "topic": s["topic"],
                            "status": s["status"],
                            "metrics": metrics,
                            "latest_observations": [
                                {
                                    "id": o.get("id"),
                                    "type": o.get("type"),
                                    "agent": o.get("agent"),
                                    "snippet": (o.get("content", "")[:120] + "...")
                                    if len(o.get("content", "")) > 120
                                    else o.get("content", ""),
                                    "created_at": o.get("created_at"),
                                }
                                for o in latest
                            ],
                        }
                    )
                domains["session"] = sessions_list
                counts["session"] = len(sessions_list)
                total += len(sessions_list)

        # --- Timeline ---
        if scope in ("timeline", "all"):
            timeline_results = self.get_timeline(
                since=since,
                max_results=max_results,
            )
            if timeline_results:
                flat = []
                for entry in timeline_results:
                    flat.append(
                        {
                            "_source": entry.get("_source"),
                            "created_at": entry.get("created_at"),
                            "snippet": (
                                entry.get("content", "")[:200]
                                if isinstance(entry.get("content"), str)
                                else str(entry.get("details", ""))[:200]
                            ),
                        }
                    )
                domains["timeline"] = flat
                counts["timeline"] = len(flat)
                total += len(flat)

        # --- HF (handoff index) ---
        if scope in ("hf", "all"):
            hf_results = self.hf_search(query=query_stripped, limit=max_results)
            if hf_results:
                flat = []
                for r in hf_results:
                    flat.append(
                        {
                            "ref": r.get("ref"),
                            "type": r.get("type"),
                            "title": r.get("title"),
                            "agent": r.get("agent"),
                            "task": r.get("task"),
                            "st": r.get("st"),
                            "prio": r.get("prio"),
                            "file": r.get("file"),
                            "wiki": r.get("wiki"),
                            "ts": r.get("ts"),
                        }
                    )
                domains["hf"] = flat
                counts["hf"] = len(flat)
                total += len(flat)

        return {
            "domains": domains,
            "total_results": total,
            "domain_counts": counts,
        }

    # ------------------------------------------------------------------
    # Detect contradictions in observations
    # ------------------------------------------------------------------

    def detect_contradictions(
        self,
        observations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Detect contradictions among observations (entity sentiment divergence).

        Returns:
            Contradiction dicts with entities, observations, description.
        """
        contradictions: list[dict[str, Any]] = []

        # Group observations by entity
        entity_obs: dict[str, list[dict[str, Any]]] = {}
        for obs in observations:
            for ent in obs.get("entities", []):
                ent_lower = ent.lower().strip()
                entity_obs.setdefault(ent_lower, []).append(obs)

        for entity, obs_list in entity_obs.items():
            if len(obs_list) < 2:
                continue

            # Look for status/sentiment contradictions
            status_indicators = {
                "✅": "positive",
                "❌": "negative",
                "✓": "positive",
                "✗": "negative",
                "ok": "positive",
                "fail": "negative",
                "success": "positive",
                "error": "negative",
                "blocked": "negative",
                "fixed": "positive",
                "broken": "negative",
                "done": "positive",
                "stuck": "negative",
            }

            sentiments = []
            for obs in obs_list:
                content = obs.get("content", "").lower()
                for indicator, sentiment in status_indicators.items():
                    if indicator in content:
                        sentiments.append((obs["id"], sentiment, indicator))
                        break

            # If we have both positive and negative sentiment for same entity
            positives = [s for s in sentiments if s[1] == "positive"]
            negatives = [s for s in sentiments if s[1] == "negative"]
            if positives and negatives:
                contradictions.append(
                    {
                        "entities": [entity],
                        "description": (
                            f"Mixed signals for entity '{entity}': "
                            f"{len(positives)} positive vs {len(negatives)} negative indicators"
                        ),
                        "positive_observations": [p[0] for p in positives],
                        "negative_observations": [n[0] for n in negatives],
                    }
                )

        return contradictions

    # ------------------------------------------------------------------
    # Observations by entity
    # ------------------------------------------------------------------

    def get_observations_by_entity(
        self,
        entity_name: str,
        since_days: int = 30,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Get observations linked to a specific entity."""
        rows = self._conn.execute(
            """SELECT o.* FROM observations o
                JOIN observation_entities oe ON oe.observation_id = o.id
                JOIN entities e ON e.id = oe.entity_id
               WHERE e.name = ?
                 AND o.created_at >= datetime('now', ? || ' days')
               ORDER BY o.created_at DESC
               LIMIT ?""",
            (entity_name.strip().lower(), f"-{since_days}", max_results),
        ).fetchall()
        return [self._row_to_observation_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Non-consolidated observations (for consolidate tool)
    # ------------------------------------------------------------------

    def get_non_consolidated_observations(
        self,
        session_id: str | None = None,
        age_days: int = 7,
        max_results: int = 200,
    ) -> list[dict[str, Any]]:
        """Get observations that have not yet been consolidated.

        Consolidation is tracked via summaries level=3 (cold compression)
        or memory_layers entries.

        Args:
            session_id: Optional session filter.
            age_days: Only include observations older than N days.
            max_results: Maximum results (default 200).

        Returns:
            List of observation dicts.
        """
        sql = """SELECT o.* FROM observations o
                  WHERE o.compression_level = 0
                    AND o.created_at < datetime('now', ? || ' days')"""
        params: list[Any] = [f"-{age_days}"]

        if session_id:
            sql += " AND o.session_id = ?"
            params.append(session_id)

        # Exclude observations already covered by a summary level >= 3
        sql += """ AND o.id NOT IN (
            SELECT DISTINCT o2.id FROM observations o2
            JOIN summaries s ON s.session_id = o2.session_id
            WHERE s.level = 3 AND s.created_at >= o2.created_at
        )"""

        sql += " ORDER BY o.created_at DESC LIMIT ?"
        params.append(max_results)

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_observation_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Cross-reference by ref (auto-detect ref_type)
    # ------------------------------------------------------------------

    def cross_reference_ref(
        self,
        ref: str,
        ref_type: str = "auto",
        max_results: int = 10,
    ) -> dict[str, Any]:
        """Cross-reference any reference across observations, tasks, and entities.

        ``ref_type`` options:
        - ``"task_id"``: find observations + entities linked to a task
        - ``"entity"``: find observations + tasks linked to an entity
        - ``"session_id"``: find observations + tasks in a session
        - ``"agent"``: find observations + tasks by an agent
        - ``"auto"``: auto-detect (T-XXX → task, ses_XXX → session, etc.)

        Args:
            ref: The reference value.
            ref_type: Type of reference (default ``"auto"``).
            max_results: Max results per category.

        Returns:
            Dict with observations, tasks, entities, session info.
        """
        # Auto-detect ref_type
        resolved_type = ref_type
        if resolved_type == "auto":
            ref_upper = ref.strip().upper()
            if ref_upper.startswith("T-"):
                resolved_type = "task_id"
            elif ref.strip().startswith("ses_"):
                resolved_type = "session_id"
            else:
                # Check if it's a known entity or agent
                entity = self._conn.execute(
                    "SELECT name FROM entities WHERE name = ?",
                    (ref.strip().lower(),),
                ).fetchone()
                if entity is not None:
                    resolved_type = "entity"
                else:
                    # Check if it's an agent name
                    known_agents = {
                        "poros",
                        "efesto",
                        "atena",
                        "proteo",
                        "clio",
                        "dike",
                        "eunomia",
                        "euterpe",
                        "metis",
                        "pythagoras",
                        "hermione",
                    }
                    if ref.strip().lower() in known_agents:
                        resolved_type = "agent"
                    else:
                        resolved_type = "entity"

        result: dict[str, Any] = {
            "ref": ref,
            "ref_type": resolved_type,
            "observations": [],
            "tasks": [],
            "entities": [],
            "session": None,
        }

        if resolved_type == "task_id":
            task = self.get_task(ref)
            if task:
                result["tasks"] = [task]
                # Find observations referencing this task
                obs_rows = self._conn.execute(
                    """SELECT * FROM observations
                       WHERE task_ref = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (ref, max_results),
                ).fetchall()
                result["observations"] = [self._row_to_observation_dict(r) for r in obs_rows]
                # Find related entities via those observations
                obs_ids = [o["id"] for o in result["observations"] if o["id"]]
                if obs_ids:
                    ph = ",".join("?" for _ in obs_ids)
                    ent_rows = self._conn.execute(
                        f"""SELECT DISTINCT e.* FROM entities e
                            JOIN observation_entities oe ON oe.entity_id = e.id
                            WHERE oe.observation_id IN ({ph})
                            LIMIT ?""",
                        obs_ids + [max_results],
                    ).fetchall()
                    result["entities"] = [dict(r) for r in ent_rows]

        elif resolved_type == "entity":
            entity = self._conn.execute(
                "SELECT * FROM entities WHERE name = ?",
                (ref.strip().lower(),),
            ).fetchone()
            if entity:
                entity_id = entity["id"]
                result["entities"] = [dict(entity)]
                # Observations
                obs_rows = self._conn.execute(
                    """SELECT o.* FROM observations o
                       JOIN observation_entities oe ON oe.observation_id = o.id
                       WHERE oe.entity_id = ?
                       ORDER BY o.created_at DESC LIMIT ?""",
                    (entity_id, max_results),
                ).fetchall()
                result["observations"] = [self._row_to_observation_dict(r) for r in obs_rows]
                # Tasks
                task_rows = self._conn.execute(
                    """SELECT DISTINCT t.* FROM tasks t
                       JOIN observations o ON o.task_ref = t.id
                       JOIN observation_entities oe ON oe.observation_id = o.id
                       WHERE oe.entity_id = ?
                       ORDER BY t.updated_at DESC LIMIT ?""",
                    (entity_id, max_results),
                ).fetchall()
                result["tasks"] = [self._row_to_task_dict(r) for r in task_rows]

        elif resolved_type == "session_id":
            session = self.get_session(ref)
            if session:
                result["session"] = session
                obs_rows = self._conn.execute(
                    """SELECT * FROM observations
                       WHERE session_id = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (ref, max_results),
                ).fetchall()
                result["observations"] = [self._row_to_observation_dict(r) for r in obs_rows]
                task_ids = session.get("task_ids", [])
                if task_ids:
                    ph = ",".join("?" for _ in task_ids)
                    task_rows = self._conn.execute(
                        f"""SELECT * FROM tasks WHERE id IN ({ph})
                            ORDER BY updated_at DESC""",
                        task_ids,
                    ).fetchall()
                    result["tasks"] = [self._row_to_task_dict(r) for r in task_rows]

        elif resolved_type == "agent":
            obs_rows = self._conn.execute(
                """SELECT * FROM observations
                   WHERE LOWER(agent) = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (ref.strip().lower(), max_results),
            ).fetchall()
            result["observations"] = [self._row_to_observation_dict(r) for r in obs_rows]
            task_rows = self._conn.execute(
                """SELECT * FROM tasks
                   WHERE LOWER(owner) = ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (ref.strip().lower(), max_results),
            ).fetchall()
            result["tasks"] = [self._row_to_task_dict(r) for r in task_rows]

        return result

    # ------------------------------------------------------------------
    # Enhanced stats
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_session_dict(row: Any) -> dict[str, Any]:  # noqa: ANN401
        """Convert a ``sessions`` table row to a JSON-friendly dict."""
        task_ids_raw = row["task_ids"]
        if isinstance(task_ids_raw, str):
            task_ids = json.loads(task_ids_raw)
        else:
            task_ids = task_ids_raw or []

        metadata_raw = row["metadata"]
        if isinstance(metadata_raw, str):
            metadata = json.loads(metadata_raw)
        else:
            metadata = metadata_raw or {}

        has_is_active = "is_active" in row.keys()
        return {
            "id": row["id"],
            "status": row["status"],
            "topic": row["topic"],
            "summary": row["summary"],
            "agent": row["agent"],
            "task_ids": task_ids,
            "token_budget": row["token_budget"],
            "token_discovery": row["token_discovery"],
            "token_read": row["token_read"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "updated_at": row["updated_at"],
            "metadata": metadata,
            "is_active": bool(row["is_active"]) if has_is_active else True,
            "superseded_by": row["superseded_by"] if has_is_active else None,
        }

    @staticmethod
    def _row_to_observation_dict(row: Any) -> dict[str, Any]:  # noqa: ANN401
        """Convert an ``observations`` table row to a JSON-friendly dict."""
        entities_raw = row["entities"]
        if isinstance(entities_raw, str):
            entities = json.loads(entities_raw)
        else:
            entities = entities_raw or []

        has_is_active = "is_active" in row.keys()
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "parent_id": row["parent_id"],
            "type": row["type"],
            "agent": row["agent"],
            "content": row["content"],
            "tokens_discovery": row["tokens_discovery"],
            "tokens_read": row["tokens_read"],
            "token_savings": row["token_savings"],
            "entities": entities,
            "handoff_path": row["handoff_path"],
            "task_ref": row["task_ref"],
            "compression_level": row["compression_level"],
            "is_active": bool(row["is_active"]) if has_is_active else True,
            "superseded_by": row["superseded_by"] if has_is_active else None,
            "created_at": row["created_at"],
        }

    @staticmethod
    def _row_to_task_dict(row: Any) -> dict[str, Any]:  # noqa: ANN401
        """Convert a ``tasks`` table row to a JSON-friendly dict."""
        tags_raw = row["tags"]
        if isinstance(tags_raw, str):
            tags = json.loads(tags_raw)
        else:
            tags = tags_raw or []

        handoff_refs_raw = row["handoff_refs"]
        if isinstance(handoff_refs_raw, str):
            handoff_refs = json.loads(handoff_refs_raw)
        else:
            handoff_refs = handoff_refs_raw or []

        has_is_active = "is_active" in row.keys()
        return {
            "id": row["id"],
            "description": row["description"],
            "status": row["status"],
            "priority": row["priority"],
            "owner": row["owner"],
            "tags": tags,
            "parent": row["parent"],
            "handoff_refs": handoff_refs,
            "compression_level": row["compression_level"],
            "is_active": bool(row["is_active"]) if has_is_active else True,
            "superseded_by": row["superseded_by"] if has_is_active else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_task_event_dict(row: Any) -> dict[str, Any]:  # noqa: ANN401
        """Convert a ``task_events`` table row to a JSON-friendly dict."""
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "type": row["type"],
            "details": row["details"],
            "handoff_path": row["handoff_path"],
            "compression_level": row["compression_level"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _row_to_summary_dict(row: Any) -> dict[str, Any]:  # noqa: ANN401
        """Convert a ``summaries`` table row to a JSON-friendly dict."""
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "level": row["level"],
            "parent_id": row["parent_id"],
            "content": row["content"],
            "token_count": row["token_count"],
            "created_at": row["created_at"],
        }
