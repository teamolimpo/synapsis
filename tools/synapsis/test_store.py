"""Smoke tests for SynapsisStore.

Tests cover:
- Database initialisation
- Session CRUD
- Task CRUD
- Observation CRUD with entity linking
- Task event logging
- Cross-reference entity linking
- FTS5 search
- Compression
- Transaction safety

Run with::

    pytest tools/synapsis/test_store.py -v --tb=short
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from tools.synapsis.store import SynapsisStore


@pytest.fixture
def store() -> Iterator[SynapsisStore]:
    """Create a SynapsisStore with a temporary database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = Path(tmp.name)
    tmp.close()

    s = SynapsisStore(db_path=db_path)
    yield s

    s.close()
    db_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Basic schema tests
# ---------------------------------------------------------------------------


def test_init_db(store: SynapsisStore) -> None:
    """Verify the database is initialised with all tables."""
    tables = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {r["name"] for r in tables}
    expected = {
        "domains",
        "sessions",
        "observations",
        "tasks",
        "task_events",
        "entities",
        "observation_entities",
        "summaries",
        "counters",
        "memory_layers",
        "observations_fts",
        "tasks_fts",
        "hf_fts",
    }
    missing = expected - table_names
    assert not missing, f"Missing tables: {missing}"


def test_init_domains(store: SynapsisStore) -> None:
    """Verify default domains are created."""
    domains = store.list_domains()
    domain_ids = {d["id"] for d in domains}
    assert domain_ids == {"session", "task", "system", "entity", "knowledge", "hf", "legacy"}
    for d in domains:
        assert d["is_active"] == 1


# ---------------------------------------------------------------------------
# Session tests
# ---------------------------------------------------------------------------


def test_create_session(store: SynapsisStore) -> None:
    """Create a session and verify fields."""
    result = store.create_session(topic="Test Session", task_ids=["T-MCP-001"])
    assert result["status"] == "active"
    assert result["topic"] == "Test Session"
    assert "ses_" in result["id"]

    # Verify persistence
    session = store.get_session(result["id"])
    assert session is not None
    assert session["topic"] == "Test Session"
    assert session["task_ids"] == ["T-MCP-001"]


def test_get_active_session(store: SynapsisStore) -> None:
    """Verify active session retrieval."""
    store.create_session(topic="First")
    s2 = store.create_session(topic="Second")
    active = store.get_active_session()
    assert active is not None
    assert active["id"] == s2["id"]


def test_update_session(store: SynapsisStore) -> None:
    """Update session fields."""
    s = store.create_session(topic="Original")
    store.update_session(s["id"], topic="Updated", status="interrupted")
    updated = store.get_session(s["id"])
    assert updated is not None
    assert updated["topic"] == "Updated"
    assert updated["status"] == "interrupted"


# ---------------------------------------------------------------------------
# Observation tests
# ---------------------------------------------------------------------------


def test_add_observation(store: SynapsisStore) -> None:
    """Create a session and add an observation."""
    s = store.create_session(topic="Obs Test")
    obs_id = store.add_observation(
        session_id=s["id"],
        type="note",
        content="This is a test observation",
        agent="efesto",
        tokens_discovery=100,
        tokens_read=50,
    )
    assert obs_id is not None
    assert isinstance(obs_id, int)

    # Verify
    obs = store.get_observation(obs_id)
    assert obs is not None
    assert obs["content"] == "This is a test observation"
    assert obs["session_id"] == s["id"]
    assert obs["type"] == "note"


def test_add_observation_with_entities(store: SynapsisStore) -> None:
    """Add observation and link entities."""
    s = store.create_session(topic="Entity Test")
    obs_id = store.add_observation(
        session_id=s["id"],
        type="decision",
        content="Working on Synapsis project",
        entities=["synapsis", "efesto"],
    )

    # Link entities
    entity_ids = []
    for name, etype in [("synapsis", "project"), ("efesto", "agent")]:
        eid = store.get_or_create_entity(name, entity_type=etype)
        store.link_entity_to_observation(obs_id, eid)
        entity_ids.append(eid)

    # Verify via cross-ref
    results = store.cross_reference_entity("synapsis")
    assert len(results["observations"]) == 1
    assert results["observations"][0]["id"] == obs_id


# ---------------------------------------------------------------------------
# Task tests
# ---------------------------------------------------------------------------


def test_create_task(store: SynapsisStore) -> None:
    """Create a task and verify fields."""
    result = store.create_task(
        task_id="T-TEST-001",
        description="Test task",
        status="pending",
        priority="high",
        owner="efesto",
        tags=["test", "synapsis"],
    )
    assert result["id"] == "T-TEST-001"
    assert result["status"] == "pend"

    # Verify persistence
    task = store.get_task("T-TEST-001")
    assert task is not None
    assert task["description"] == "Test task"
    assert task["priority"] == "high"
    assert task["owner"] == "efesto"
    assert task["tags"] == ["test", "synapsis"]


def test_next_task_id(store: SynapsisStore) -> None:
    """Verify counter-based ID generation."""
    tid1 = store.next_task_id("MCP")
    assert tid1 == "T-MCP-001"
    tid2 = store.next_task_id("MCP")
    assert tid2 == "T-MCP-002"
    tid3 = store.next_task_id("API")
    assert tid3 == "T-API-001"


def test_update_task_status(store: SynapsisStore) -> None:
    """Update task status with state machine."""
    store.create_task("T-STATUS-001", "Status test", status="pending")

    result = store.update_task_status("T-STATUS-001", "in_progress")
    assert result["new_status"] == "prog"

    result = store.update_task_status("T-STATUS-001", "completed")
    assert result["new_status"] == "done"

    # Verify events (short canonical)
    events = store.get_task_events("T-STATUS-001")
    assert len(events) >= 3  # created + status_change × 2
    status_changes = [e for e in events if e["type"] == "sc"]
    assert len(status_changes) == 2


def test_parent_auto_promotion(store: SynapsisStore) -> None:
    """Verify parent task auto-promotes when children complete."""
    store.create_task("T-PARENT-001", "Parent task", status="in_progress")
    store.create_task("T-CHILD-001", "Child 1", parent="T-PARENT-001")
    store.create_task("T-CHILD-002", "Child 2", parent="T-PARENT-001")

    store.update_task_status("T-CHILD-001", "completed")
    store.update_task_status("T-CHILD-002", "completed")

    parent = store.get_task("T-PARENT-001")
    assert parent is not None
    assert parent["status"] == "done"


# ---------------------------------------------------------------------------
# Task event tests
# ---------------------------------------------------------------------------


def test_add_task_event(store: SynapsisStore) -> None:
    """Add events to a task."""
    store.create_task("T-EVENT-001", "Event test")
    result = store.add_task_event(
        task_id="T-EVENT-001",
        event_type="note",
        details="This is a test note",
    )
    assert result["task_id"] == "T-EVENT-001"
    assert result["event_type"] == "note"

    events = store.get_task_events("T-EVENT-001")
    assert len(events) >= 2  # created event + note event
    notes = [e for e in events if e["type"] == "note"]
    assert len(notes) == 1


# ---------------------------------------------------------------------------
# Entity cross-reference tests
# ---------------------------------------------------------------------------


def test_cross_reference(store: SynapsisStore) -> None:
    """Cross-reference entity across observations and tasks."""
    # Create session with observation linked to entity
    s = store.create_session(topic="Cross-ref test")
    obs_id = store.add_observation(
        session_id=s["id"],
        type="note",
        content="Observation about chimera project",
        task_ref="T-CHIMERA-001",
    )

    # Create entity and link
    eid = store.get_or_create_entity("chimera", entity_type="project")
    store.link_entity_to_observation(obs_id, eid)

    # Create a task
    store.create_task("T-CHIMERA-001", "Chimera project task")

    # Cross-reference
    results = store.cross_reference_entity("chimera")
    assert len(results["observations"]) == 1
    assert len(results["tasks"]) >= 1


# ---------------------------------------------------------------------------
# FTS5 search tests
# ---------------------------------------------------------------------------


def test_search_observations(store: SynapsisStore) -> None:
    """FTS5 search across observations."""
    s = store.create_session(topic="Search test")
    store.add_observation(s["id"], "note", "Hello world from Synapsis")
    store.add_observation(s["id"], "note", "Another observation")
    store.add_observation(s["id"], "decision", "Decided to build Synapsis")

    results = store.search_observations("synapsis")
    assert len(results) >= 2


def test_search_tasks(store: SynapsisStore) -> None:
    """FTS5 search across tasks."""
    store.create_task("T-SEARCH-001", "Implement full text search for tasks")
    store.create_task("T-SEARCH-002", "Write documentation for API")

    results = store.search_tasks("search")
    assert len(results) >= 1
    assert results[0]["id"] == "T-SEARCH-001"


# ---------------------------------------------------------------------------
# Stats tests
# ---------------------------------------------------------------------------


def test_get_stats(store: SynapsisStore) -> None:
    """Verify aggregate statistics."""
    s = store.create_session(topic="Stats test")
    store.add_observation(s["id"], "note", "Observation 1")
    store.add_observation(s["id"], "note", "Observation 2")
    store.create_task("T-STATS-001", "Stats task")

    stats = store.get_stats()
    assert stats["sessions"]["total"] >= 1
    assert stats["observations"] >= 2
    assert stats["tasks"]["total"] >= 1
    assert stats["tasks"]["by_status"].get("pend", 0) >= 1


# ---------------------------------------------------------------------------
# Memory layer tests
# ---------------------------------------------------------------------------


def test_memory_layers(store: SynapsisStore) -> None:
    """Set and retrieve memory layers."""
    s = store.create_session(topic="Layer test")
    layer_id = store.set_memory_layer(
        session_id=s["id"],
        layer="context",
        content="This is the context layer",
    )
    assert layer_id is not None

    layers = store.get_memory_layers(s["id"])
    assert len(layers) == 1
    assert layers[0]["layer"] == "context"
    assert layers[0]["content"] == "This is the context layer"


# ---------------------------------------------------------------------------
# Compression tests
# ---------------------------------------------------------------------------


def test_compress_dry_run(store: SynapsisStore) -> None:
    """Compression dry-run should not modify data."""
    s = store.create_session(topic="Compress test")
    store.add_observation(s["id"], "note", "A" * 200)

    # Dry run should report observations_warm without modifying
    result = store.compress_observations(age_days=3650, max_level=1, dry_run=True)
    assert result["dry_run"] is True

    # Verify no actual compression happened (content unchanged)
    obs_list = store.get_observations(s["id"], limit=10)
    assert len(obs_list[0]["content"]) == 200  # unchanged


def test_domain_gating(store: SynapsisStore) -> None:
    """Enable/disable domains."""
    store.set_domain_active("session", False)
    domain = store.get_domain("session")
    assert domain is not None
    assert domain["is_active"] == 0

    store.set_domain_active("session", True)
    domain = store.get_domain("session")
    assert domain is not None
    assert domain["is_active"] == 1


# ---------------------------------------------------------------------------
# Session-tasks cross reference
# ---------------------------------------------------------------------------


def test_get_session_tasks(store: SynapsisStore) -> None:
    """Get tasks associated with a session."""
    s = store.create_session(topic="Session tasks", task_ids=["T-SESSTASK-001"])
    store.create_task("T-SESSTASK-001", "Session-linked task")

    # Also add via observation.task_ref
    store.add_observation(
        session_id=s["id"],
        type="note",
        content="Referencing another task",
        task_ref="T-SESSTASK-002",
    )
    store.create_task("T-SESSTASK-002", "Referenced task")

    tasks = store.get_session_tasks(s["id"])
    task_ids = {t["id"] for t in tasks}
    assert "T-SESSTASK-001" in task_ids
    assert "T-SESSTASK-002" in task_ids


# ---------------------------------------------------------------------------
# Query tasks with filters
# ---------------------------------------------------------------------------


def test_query_tasks(store: SynapsisStore) -> None:
    """Query tasks with various filters."""
    store.create_task("T-QUERY-001", "Fix bug", priority="high", owner="efesto", status="pending")
    store.create_task(
        "T-QUERY-002", "Write docs", priority="low", owner="atena", status="completed"
    )
    store.create_task(
        "T-QUERY-003", "Refactor code", priority="medium", owner="efesto", status="in_progress"
    )

    # Filter by owner
    efesto_tasks = store.get_tasks(owner="efesto")
    assert len(efesto_tasks) == 2

    # Filter by status
    completed = store.get_tasks(status="completed")
    assert len(completed) == 1
    assert completed[0]["id"] == "T-QUERY-002"

    # Filter by priority
    high_tasks = store.get_tasks(priority="high")
    assert len(high_tasks) == 1

    # Filter by search
    docs_tasks = store.get_tasks(search="docs")
    assert len(docs_tasks) == 1


# ---------------------------------------------------------------------------
# Transaction safety
# ---------------------------------------------------------------------------


def test_transaction_rollback(store: SynapsisStore) -> None:
    """Verify transaction rollback on error.

    Uses a manual approach: add_observation internally does commit(),
    so we simulate a multi-step transaction by inserting directly
    via the connection and rolling back on error.
    """
    s = store.create_session(topic="TX test")
    session_id = s["id"]

    # Manually rollback after a successful insert to verify
    # that a rollback undoes the insert

    try:
        with store.transaction():
            store._conn.execute(
                """INSERT INTO observations
                   (session_id, type, agent, content, entities, created_at)
                   VALUES (?, 'note', 'Poros', ?, '[]', ?)""",
                (session_id, "Rollback test", now_iso()),
            )
            msg = "Intentional error"
            raise RuntimeError(msg)
    except RuntimeError:
        pass

    # Verify no observations were persisted
    obs_list = store.get_observations(session_id, limit=10)
    assert len(obs_list) == 0


def now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format with microseconds."""
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")


# ---------------------------------------------------------------------------
# Vacuum
# ---------------------------------------------------------------------------


def test_vacuum(store: SynapsisStore) -> None:
    """Run VACUUM on the database."""
    store.create_session(topic="Vacuum test")
    result = store.vacuum()
    assert result["path"] is not None
    assert result["size_before"] > 0
    assert result["size_after"] > 0


# ---------------------------------------------------------------------------
# Auto-consolidation tests
# ---------------------------------------------------------------------------


def test_auto_consolidate_skips_when_no_candidates(store: SynapsisStore) -> None:
    """auto_consolidate_if_needed returns noop when there are no observations."""
    result = store.auto_consolidate_if_needed(session_id=None)
    assert result["triggered"] is False
    assert result["consolidated"] == 0


def test_auto_consolidate_skips_when_already_consolidating(store: SynapsisStore) -> None:
    """auto_consolidate_if_needed skips when a consolidation is already in progress."""
    store._consolidating = True
    try:
        result = store.auto_consolidate_if_needed(session_id=None)
        assert result["triggered"] is False
        assert result["reason"] == "already consolidating"
    finally:
        store._consolidating = False


def test_auto_consolidate_triggers_when_candidates_above_20(
    store: SynapsisStore,
) -> None:
    """Auto-consolidation triggers when there are more than 20 unconsolidated obs."""
    # Enable WAL so DDL works
    s = store.create_session(topic="Auto-consolidate bulk test")

    # Add 25 observations with old timestamps
    from datetime import UTC, datetime, timedelta

    old_ts = (datetime.now(UTC) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S.%f")
    for i in range(25):
        store._conn.execute(
            """INSERT INTO observations
               (session_id, type, agent, content, entities, compression_level, created_at)
               VALUES (?, 'note', 'efesto', ?, '[]', 0, ?)""",
            (s["id"], f"Bulk observation {i}", old_ts),
        )
    store._conn.commit()

    # Reset counter to ensure we don't hit rate limit
    store._auto_consolidate_counter = 0

    result = store.auto_consolidate_if_needed(session_id=s["id"])
    assert result["triggered"] is True, f"Should have triggered, got: {result}"
    assert result["consolidated"] > 0, "Should have consolidated observations"
    assert "unconsolidated (25)" in result["reason"] or "> 20" in result["reason"]


def test_auto_consolidate_rate_limiting(store: SynapsisStore) -> None:
    """Rate limiter only triggers auto-consolidate every 10th call via counter."""
    s = store.create_session(topic="Rate limit test")

    # Reset counter to just before a trigger point (10, 20, 30...)
    store._auto_consolidate_counter = 9

    # add_observation increments counter and checks every 10th call.
    # add 5 observations → counter goes 9 → 14, no trigger point passed
    for i in range(5):
        store.add_observation(
            session_id=s["id"],
            type="note",
            content=f"Rate limit obs {i}",
        )
    # counter should be 9 + 5 = 14
    assert store._auto_consolidate_counter == 14

    # Add 5 more → counter goes 14 → 19, trigger at 20 (not yet)
    for i in range(5):
        store.add_observation(
            session_id=s["id"],
            type="note",
            content=f"Rate limit obs batch2 {i}",
        )
    # counter should be 19 (no trigger at 20 yet)
    assert store._auto_consolidate_counter == 19

    # Add 1 more → counter goes 19 → 20, trigger happens
    store.add_observation(
        session_id=s["id"],
        type="note",
        content="Rate limit obs trigger",
    )
    assert store._auto_consolidate_counter == 20


def test_auto_consolidate_lock_unlocks(store: SynapsisStore) -> None:
    """The _consolidating flag is reset after auto-consolidation completes."""
    s = store.create_session(topic="Lock test")

    from datetime import UTC, datetime, timedelta

    old_ts = (datetime.now(UTC) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S.%f")
    for i in range(25):
        store._conn.execute(
            """INSERT INTO observations
               (session_id, type, agent, content, entities, compression_level, created_at)
               VALUES (?, 'note', 'efesto', ?, '[]', 0, ?)""",
            (s["id"], f"Lock test obs {i}", old_ts),
        )
    store._conn.commit()

    store._auto_consolidate_counter = 0
    result = store.auto_consolidate_if_needed(session_id=s["id"])
    assert result["triggered"] is True
    assert store._consolidating is False, "Lock should be released after consolidation"
