"""Embedding generation and sqlite-vec vector index for chunk search.

Provides lazy-loaded SentenceTransformer model, sqlite-vec virtual table
management, and batch/single embedding indexing operations.
"""

from __future__ import annotations

import sqlite3

from loguru import logger

# ---------------------------------------------------------------------------
# Lazy model singleton  (SentenceTransformer import is HEAVY — ~8s for torch +
# transformers + tokenizers. Keep it inside get_model() so importing this
# module is instant unless you actually call the function.)
# ---------------------------------------------------------------------------

_MODEL: object = None  # Cached SentenceTransformer instance (lazy-loaded)
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def get_model():  # noqa: ANN201  # return type omitted intentionally (heavy import)
    """Return the SentenceTransformer model (lazy singleton).

    The model is loaded once and cached for the lifetime of the process.
    Downloads from HuggingFace Hub on first use (~90 MB), cached in
    ``~/.cache/huggingface/hub/``.

    Raises:
        RuntimeError: If ``sentence-transformers`` is not installed.

    The SentenceTransformer type is intentionally omitted from the
    return annotation to avoid a module-level import (which takes ~8s
    due to torch + transformers + tokenizers).
    """
    global _MODEL
    if _MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.warning("sentence-transformers not available")
            raise RuntimeError(
                "sentence-transformers not installed. Run: uv sync --group embeddings"
            ) from None

        logger.info(f"Loading embedding model: {_MODEL_NAME}")
        _MODEL = SentenceTransformer(_MODEL_NAME)
        logger.info("Embedding model loaded")
    return _MODEL


# ---------------------------------------------------------------------------
# sqlite-vec table management
# ---------------------------------------------------------------------------


def ensure_vec_table(conn: sqlite3.Connection) -> None:
    """Load sqlite-vec extension and create ``chunks_vec`` virtual table.

    The virtual table stores 384-dimensional float embeddings (matching
    all-MiniLM-L6-v2 output dimension).

    Raises:
        RuntimeError: If ``sqlite-vec`` is not installed.

    Args:
        conn: SQLite connection with the chunks schema.
    """
    try:
        import sqlite_vec  # noqa: PLC0415
    except ImportError:
        logger.warning("sqlite-vec not available")
        raise RuntimeError("sqlite-vec not installed. Run: uv sync --group embeddings") from None

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute(
        """CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
            chunk_id TEXT PRIMARY KEY,
            embedding float[384]
        )"""
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Indexing operations
# ---------------------------------------------------------------------------


def index_all_embeddings(conn: sqlite3.Connection) -> int:
    """Generate embeddings for all chunks that lack a vector.

    Selects chunks not yet present in ``chunks_vec`` (via LEFT JOIN),
    generates their embedding in batch via SentenceTransformer, and
    inserts them into the vector table.

    Args:
        conn: SQLite connection with vec0 table loaded.

    Returns:
        Number of embeddings generated and indexed.
    """
    cur = conn.cursor()
    rows = cur.execute(
        """SELECT c.id, c.content
           FROM chunks c
           LEFT JOIN chunks_vec v ON v.chunk_id = c.id
           WHERE v.chunk_id IS NULL"""
    ).fetchall()

    if not rows:
        return 0

    model = get_model()
    texts = [r["content"] for r in rows]
    chunk_ids = [r["id"] for r in rows]

    logger.info(f"Generating {len(texts)} embeddings...")
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)

    for cid, emb in zip(chunk_ids, embeddings, strict=True):
        # sqlite-vec expects raw float32 bytes
        cur.execute(
            "INSERT OR REPLACE INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
            (cid, emb.astype("float32").tobytes()),
        )
    conn.commit()

    return len(texts)


def index_chunk_embedding(conn: sqlite3.Connection, chunk_id: str, content: str) -> None:
    """Generate and store embedding for a single chunk.

    Args:
        conn: SQLite connection with vec0 table loaded.
        chunk_id: ID of the chunk in the ``chunks`` table.
        content: Content text to embed.
    """
    model = get_model()
    emb = model.encode([content], normalize_embeddings=True)[0]
    conn.execute(
        "INSERT OR REPLACE INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
        (chunk_id, emb.astype("float32").tobytes()),
    )
    conn.commit()


def clean_orphan_vectors(conn: sqlite3.Connection) -> int:
    """Remove vectors for chunk IDs that no longer exist in ``chunks`` table.

    Args:
        conn: SQLite connection with vec0 table loaded.

    Returns:
        Number of vectors removed.
    """
    cur = conn.cursor()
    cur.execute(
        """DELETE FROM chunks_vec
           WHERE chunk_id IN (
               SELECT v.chunk_id
               FROM chunks_vec v
               LEFT JOIN chunks c ON c.id = v.chunk_id
               WHERE c.id IS NULL
           )"""
    )
    removed = cur.rowcount
    conn.commit()
    return removed
