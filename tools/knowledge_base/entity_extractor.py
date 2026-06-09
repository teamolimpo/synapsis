"""Entity extraction from text via dictionary lookup + regex patterns.

Provides a 3-level pipeline (no spaCy) for extracting known and potential
entities from knowledge base content and queries:

- **Level 1** — Custom YAML dictionary with known Team Olimpo entities
  (projects, tools, people, concepts).
- **Level 2** — Regex patterns for proper nouns, CamelCase identifiers,
  acronyms, and quoted text.
- **Level 3** — Fuzzy matching via ``thefuzz`` (optional, query-time only).

Usage::

    from tools.knowledge_base.entity_extractor import extract_entities

    entities = extract_entities("Mem0 usa multi-signal retrieval e MCP")
    # → [{"entity_text": "Mem0", "entity_type": "PROJECT", ...}, ...]
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

# ---------------------------------------------------------------------------
# Dictionary loading
# ---------------------------------------------------------------------------

DICTIONARY_PATH = Path(__file__).resolve().parent / "entity_dictionary.yaml"

# Stoplist for proper noun first-words — common English words that appear
# capitalised at sentence start but are not domain entities.
_PROPER_STOPLIST: frozenset[str] = frozenset(
    {
        "Both",
        "This",
        "That",
        "These",
        "Those",
        "What",
        "When",
        "Where",
        "How",
        "Why",
        "Which",
        "Some",
        "Many",
        "Most",
        "Each",
        "Every",
        "All",
        "Such",
        "The",
        "A",
        "An",
        "Just",
        "Only",
        "Also",
        "Here",
        "There",
        "Then",
        "Thus",
        "Hence",
        "First",
        "Second",
        "Third",
        "Next",
        "Last",
        "Finally",
        "However",
        "Moreover",
        "Furthermore",
        "Nevertheless",
        "Therefore",
        "Because",
        "Although",
        "While",
        "Since",
        "After",
        "Before",
        "During",
        "Within",
        "Without",
        "Note",
        "See",
        "Figure",
        "Table",
        "Chapter",
        "Section",
        "Part",
        "Step",
        "Phase",
        "Stage",
        "Option",
        "Flag",
        "Example",
        "Usage",
        "Warning",
        "Tip",
        "Info",
    }
)

# Stoplist for ALL CAPS acronym matching — common English words that
# are not domain entities.
_ACRONYM_STOPLIST: frozenset[str] = frozenset(
    {
        "I",
        "A",
        "THE",
        "THIS",
        "THAT",
        "AND",
        "OR",
        "FOR",
        "NOT",
        "ALL",
        "ARE",
        "CAN",
        "HAS",
        "HAD",
        "ITS",
        "OUR",
        "SHE",
        "HER",
        "HIM",
        "WHO",
        "BUT",
        "YOU",
        "USE",
        "VIA",
        "PER",
        "OUT",
        "NEW",
        "ONE",
        "TWO",
        "ANY",
        "MAY",
        "NOW",
        "HOW",
        "WHY",
        "WAS",
        "HIS",
        "SIR",
        "MRS",
        "MR",
        "DR",
        "LTD",
        "INC",
        "ETC",
        "E_G",
        "I_E",
        "VS",
    }
)


_DICT_CACHE: dict[str, dict[str, list[dict[str, Any]]]] = {}


def load_dictionary(path: str | Path = DICTIONARY_PATH) -> dict[str, list[dict[str, Any]]]:
    """Load entity dictionary from a YAML file (cached after first load).

    The YAML file must have top-level keys for categories (e.g. ``projects``,
    ``tools``, ``people``, ``concepts``), each containing a list of entries
    with ``name``, ``type`` and optional ``aliases``.

    The dictionary is cached in memory after the first load — subsequent calls
    with the same path return immediately.

    Args:
        path: Path to the YAML dictionary file.

    Returns:
        Dictionary mapping category names to lists of entity entries.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        yaml.YAMLError: If the YAML content is malformed.
    """
    p = Path(path)
    cache_key = str(p.resolve())
    if cache_key in _DICT_CACHE:
        return _DICT_CACHE[cache_key]

    if not p.exists():
        raise FileNotFoundError(f"Entity dictionary not found: {p}")
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML dict, got {type(data).__name__}")
    entry_count = sum(len(v) for v in data.values())
    logger.info(f"Loaded entity dictionary ({entry_count} entries)")
    _DICT_CACHE[cache_key] = data
    return data


# ---------------------------------------------------------------------------
# Level 1 — Dictionary lookup (source_level=1)
# ---------------------------------------------------------------------------


def _dict_lookup(text: str, dictionary: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Match exact dictionary entries and their aliases in *text*.

    Args:
        text: The text to scan.
        dictionary: Loaded entity dictionary (from :func:`load_dictionary`).

    Returns:
        List of entity dicts with keys ``entity_text``, ``entity_type``,
        ``raw_text``, ``source_level``.
    """
    entities: list[dict[str, Any]] = []
    seen: set[str] = set()
    text_lower = text.lower()

    for _category, entries in dictionary.items():
        for entry in entries:
            names: list[str] = [entry["name"].lower()]
            names.extend(a.lower() for a in entry.get("aliases", []))

            for name in names:
                if name not in text_lower:
                    continue
                if name in seen:
                    continue
                seen.add(name)

                # Find the original case-preserved raw text
                idx = text_lower.index(name)
                raw = text[idx : idx + len(name)]

                entities.append(
                    {
                        "entity_text": entry["name"],
                        "entity_type": entry["type"],
                        "raw_text": raw,
                        "source_level": 1,
                    }
                )

    return entities


# ---------------------------------------------------------------------------
# Level 2 — Regex patterns (source_level=2)
# ---------------------------------------------------------------------------


# Pattern 2a: Proper noun sequences — consecutive Capitalized words
# Examples: "Progressive Disclosure", "Alex Garcia", "Multi-Signal Retrieval"
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b")

# Pattern 2b: CamelCase identifiers (starts lowercase, has internal uppercase)
# Examples: "sqliteVec", "hybridSearch"
_CAMEL_CASE_RE = re.compile(r"\b[a-z]+[A-Z][a-zA-Z]*\b")

# Pattern 2c: PascalCase identifiers (starts uppercase, has internal uppercase)
# Examples: "ChunkDragon", "SentenceTransformer", "FastMCP"
_PASCAL_CASE_RE = re.compile(r"\b[A-Z][a-z]+[A-Z][a-zA-Z]*\b")

# Pattern 2d: ALL CAPS identifiers (acronyms, abbreviations)
# Examples: "FTS5", "MCP", "RRF", "KNN", "NER"
_ALL_CAPS_RE = re.compile(r"\b[A-Z0-9]{2,}\b")

# Pattern 2d: Double-quoted text
_QUOTED_DOUBLE_RE = re.compile(r'"([^"]{3,})"')

# Pattern 2d (cont.): Single-quoted text
_QUOTED_SINGLE_RE = re.compile(r"'([^']{3,})'")


def _pattern_extract(text: str) -> list[dict[str, Any]]:
    """Extract entities via regex patterns (Level 2).

    Detects proper noun sequences, CamelCase identifiers, ALL CAPS acronyms,
    and quoted text.

    Args:
        text: The text to scan.

    Returns:
        List of entity dicts with keys ``entity_text``, ``entity_type``,
        ``raw_text``, ``source_level``.
    """
    entities: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 2a. Proper noun sequences
    for m in _PROPER_NOUN_RE.finditer(text):
        raw = m.group()
        words = raw.split()
        # Trim leading stop words (common English words capitalised at
        # sentence start) so "Both Progressive Disclosure" → "Progressive Disclosure"
        while words and words[0] in _PROPER_STOPLIST:
            words = words[1:]
        if not words:
            continue
        trimmed = " ".join(words)
        key = trimmed.lower()
        if key not in seen and len(trimmed) > 3:
            seen.add(key)
            entities.append(
                {
                    "entity_text": trimmed,
                    "entity_type": "PROPER",
                    "raw_text": trimmed,
                    "source_level": 2,
                }
            )

    # 2b. CamelCase identifiers (starts lowercase)
    for m in _CAMEL_CASE_RE.finditer(text):
        raw = m.group()
        key = raw.lower()
        if key not in seen and len(raw) > 3:
            seen.add(key)
            entities.append(
                {
                    "entity_text": raw,
                    "entity_type": "IDENTIFIER",
                    "raw_text": raw,
                    "source_level": 2,
                }
            )

    # 2c. PascalCase identifiers (starts uppercase, has internal uppercase)
    for m in _PASCAL_CASE_RE.finditer(text):
        raw = m.group()
        key = raw.lower()
        if key not in seen and len(raw) > 3:
            seen.add(key)
            entities.append(
                {
                    "entity_text": raw,
                    "entity_type": "IDENTIFIER",
                    "raw_text": raw,
                    "source_level": 2,
                }
            )

    # 2e. ALL CAPS / acronyms
    for m in _ALL_CAPS_RE.finditer(text):
        raw = m.group()
        if raw in _ACRONYM_STOPLIST:
            continue
        key = raw.lower()
        if key not in seen and len(raw) >= 2:
            seen.add(key)
            entities.append(
                {
                    "entity_text": raw,
                    "entity_type": "ACRONYM",
                    "raw_text": raw,
                    "source_level": 2,
                }
            )

    # 2f. Double-quoted text
    for m in _QUOTED_DOUBLE_RE.finditer(text):
        raw = m.group(1).strip()
        key = raw.lower()
        if key not in seen:
            seen.add(key)
            entities.append(
                {
                    "entity_text": raw,
                    "entity_type": "QUOTED",
                    "raw_text": f'"{raw}"',
                    "source_level": 2,
                }
            )

    # 2g. Single-quoted text
    for m in _QUOTED_SINGLE_RE.finditer(text):
        raw = m.group(1).strip()
        key = raw.lower()
        if key not in seen and len(key) > 3:
            seen.add(key)
            entities.append(
                {
                    "entity_text": raw,
                    "entity_type": "QUOTED",
                    "raw_text": f"'{raw}'",
                    "source_level": 2,
                }
            )

    return entities


# ---------------------------------------------------------------------------
# Level 3 — Fuzzy matching (source_level=3, query-time optional)
# ---------------------------------------------------------------------------


def _fuzzy_match(
    query_entity: str,
    db_entities: list[str],
    threshold: int = 85,
) -> list[str]:
    """Fuzzy-match a query entity against known entities via ``thefuzz``.

    Uses ``fuzz.token_sort_ratio`` to match regardless of word order.

    Args:
        query_entity: Entity text from the query.
        db_entities: List of known entity texts from the database.
        threshold: Minimum similarity ratio (0-100, default 85).

    Returns:
        List of matching entity texts from *db_entities*.
    """
    try:
        from thefuzz import fuzz  # noqa: PLC0415
    except ImportError:
        logger.debug("thefuzz not available — skipping fuzzy match")
        return []

    matches: list[str] = []
    q_lower = query_entity.lower()
    for de in db_entities:
        ratio = fuzz.token_sort_ratio(q_lower, de.lower())
        if ratio >= threshold:
            matches.append(de)
    return matches


# ---------------------------------------------------------------------------
# Main extraction pipeline
# ---------------------------------------------------------------------------


def extract_entities(
    text: str,
    dictionary: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Extract entities from *text* using the 2-level pipeline (dict + pattern).

    Runs Level 1 (dictionary lookup) first if a dictionary is provided,
    then Level 2 (regex pattern extraction). Results are deduplicated by
    normalized entity text — dictionary matches take priority for the
    same entity text.

    Args:
        text: The text to extract entities from.
        dictionary: Optional entity dictionary for Level 1 matching.
            When ``None``, only Level 2 pattern extraction is used.

    Returns:
        List of dicts, each with keys:
        ``entity_text`` (normalized), ``entity_type``, ``raw_text``,
        ``source_level`` (1=dictionary, 2=pattern).
    """
    if not text or not text.strip():
        return []

    all_entities: list[dict[str, Any]] = []

    # Level 1: dictionary lookup
    if dictionary:
        all_entities.extend(_dict_lookup(text, dictionary))

    # Level 2: pattern extraction
    all_entities.extend(_pattern_extract(text))

    # Deduplicate: keep first occurrence (dictionary match wins)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for ent in all_entities:
        key = ent["entity_text"].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(ent)

    return deduped


def extract_entities_batch(
    texts: list[str],
    dictionary: dict[str, list[dict[str, Any]]] | None = None,
) -> list[list[dict[str, Any]]]:
    """Extract entities from multiple texts using the 2-level pipeline.

    Args:
        texts: List of texts to extract entities from.
        dictionary: Optional entity dictionary for Level 1 matching.

    Returns:
        List of entity lists, one per input text.
    """
    return [extract_entities(t, dictionary) for t in texts]


# ---------------------------------------------------------------------------
# Database table management
# ---------------------------------------------------------------------------

ENTITIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chunk_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    entity_text TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    frequency INTEGER DEFAULT 1,
    source_level INTEGER DEFAULT 1
);
"""

ENTITIES_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_chunk_entities_text ON chunk_entities(entity_text)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_entities_chunk ON chunk_entities(chunk_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_entities_type ON chunk_entities(entity_type)",
]


def ensure_entities_table(conn: sqlite3.Connection) -> None:
    """Create the ``chunk_entities`` table and indexes if they don't exist.

    Args:
        conn: SQLite connection to the chunks database.
    """
    conn.executescript(ENTITIES_TABLE_SQL)
    for idx_sql in ENTITIES_INDEXES_SQL:
        conn.execute(idx_sql)
    conn.commit()
    logger.debug("Ensured chunk_entities table + indexes")


# ---------------------------------------------------------------------------
# Batch indexing
# ---------------------------------------------------------------------------


def extract_and_insert(
    conn: sqlite3.Connection,
    chunk_id: str,
    content: str,
    dictionary: dict[str, list[dict[str, Any]]] | None = None,
) -> int:
    """Extract entities from *content* and insert into ``chunk_entities``.

    Removes any existing entities for this chunk before inserting.

    Args:
        conn: SQLite connection.
        chunk_id: ID of the chunk in the ``chunks`` table.
        content: Content text to extract entities from.
        dictionary: Optional dictionary for Level 1 matching.

    Returns:
        Number of entity rows inserted.
    """
    entities = extract_entities(content, dictionary)
    if not entities:
        return 0

    cur = conn.cursor()
    # Remove old entities for this chunk
    cur.execute("DELETE FROM chunk_entities WHERE chunk_id = ?", (chunk_id,))

    inserted = 0
    for ent in entities:
        try:
            cur.execute(
                """INSERT INTO chunk_entities
                   (chunk_id, entity_text, entity_type, raw_text, frequency, source_level)
                   VALUES (?, ?, ?, ?, 1, ?)""",
                (
                    chunk_id,
                    ent["entity_text"].lower(),
                    ent["entity_type"],
                    ent["raw_text"],
                    ent["source_level"],
                ),
            )
            inserted += 1
        except sqlite3.Error as e:
            logger.warning(f"Failed to insert entity for chunk {chunk_id}: {e}")
    conn.commit()
    return inserted


def index_chunk_entity(
    conn: sqlite3.Connection,
    chunk_id: str,
    content: str,
    dictionary: dict[str, list[dict[str, Any]]] | None = None,
) -> int:
    """Extract and index entities for a single chunk.

    A convenience wrapper around :func:`extract_and_insert`.

    Args:
        conn: SQLite connection.
        chunk_id: ID of the chunk.
        content: Content text.
        dictionary: Optional dictionary for Level 1 matching.

    Returns:
        Number of entity rows inserted.
    """
    return extract_and_insert(conn, chunk_id, content, dictionary)


def index_all_entities(
    conn: sqlite3.Connection,
    dictionary: dict[str, list[dict[str, Any]]] | None = None,
) -> int:
    """Extract entities for all chunks that don't have any yet.

    Selects chunks missing from ``chunk_entities`` (via LEFT JOIN),
    extracts entities via the 2-level pipeline, and inserts them.

    Args:
        conn: SQLite connection with ``chunks`` and ``chunk_entities`` tables.
        dictionary: Optional dictionary for Level 1 matching.

    Returns:
        Total number of entity rows inserted.
    """
    cur = conn.cursor()
    rows = cur.execute(
        """SELECT c.id, c.content
           FROM chunks c
           LEFT JOIN chunk_entities e ON c.id = e.chunk_id
           WHERE e.chunk_id IS NULL
           GROUP BY c.id"""
    ).fetchall()

    if not rows:
        logger.info("All chunks already have entities — nothing to index")
        return 0

    logger.info(f"Indexing entities for {len(rows)} chunks...")
    total = 0
    for chunk_id, content in rows:
        n = extract_and_insert(conn, chunk_id, content, dictionary)
        total += n

    logger.info(f"Indexed {total} entities across {len(rows)} chunks")
    return total


def clean_orphan_entities(conn: sqlite3.Connection) -> int:
    """Remove entity rows for chunk IDs that no longer exist in ``chunks``.

    Args:
        conn: SQLite connection.

    Returns:
        Number of entity rows removed.
    """
    cur = conn.cursor()
    cur.execute(
        """DELETE FROM chunk_entities
           WHERE chunk_id IN (
               SELECT e.chunk_id
               FROM chunk_entities e
               LEFT JOIN chunks c ON c.id = e.chunk_id
               WHERE c.id IS NULL
           )"""
    )
    removed = cur.rowcount
    conn.commit()
    logger.info(f"Cleaned {removed} orphan entity rows")
    return removed
