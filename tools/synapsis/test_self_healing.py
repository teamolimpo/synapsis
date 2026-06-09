"""Tests for Synapsis Fase 4.2 — Self-Healing Memory.

Covers:
1. ``db_health_check`` — quick, full, repair
2. ``checkpoint_create`` + ``checkpoint_restore`` — SAVEPOINT + rollback
3. ``safe_execute`` — success and failure+rollback
4. ``compute_health_score`` — on healthy DB
5. ADD-only soft-supersede — ``update_task``, verify is_active=0 + superseded_by
6. ``orphan_scan`` — detect stale tasks
7. ``db_set_pragma`` — runtime PRAGMA configuration

Run with::

    pytest tools/synapsis/test_self_healing.py -v --tb=short
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


# ===================================================================
# 1. db_health_check
# ===================================================================


class TestDbHealthCheck:
    """PRAGMA-based health checks."""

    def test_health_check_quick(self, store: SynapsisStore) -> None:
        """``db_health_check("quick")`` should return passed=True on fresh DB."""
        result = store.db_health_check("quick")
        assert result["cmd"] == "quick"
        assert result["passed"] is True
        assert result["recovery_attempted"] is False
        assert isinstance(result["duration_ms"], float)

    def test_health_check_full(self, store: SynapsisStore) -> None:
        """``db_health_check("full")`` should return passed=True on fresh DB."""
        result = store.db_health_check("full")
        assert result["cmd"] == "full"
        assert result["passed"] is True
        assert result["recovery_attempted"] is False

    def test_health_check_repair(self, store: SynapsisStore) -> None:
        """``db_health_check("repair")`` should attempt recovery."""
        result = store.db_health_check("repair")
        assert result["cmd"] == "repair"
        assert result["recovery_attempted"] is True
        # On a healthy DB, recovery should succeed
        assert result["recovery_success"] is True

    def test_health_check_unknown_cmd(self, store: SynapsisStore) -> None:
        """Unknown cmd should return passed=False."""
        result = store.db_health_check("bogus")
        assert result["passed"] is False
        assert "unknown" in result["message"].lower()


# ===================================================================
# 2. Checkpoint / Rollback
# ===================================================================


class TestCheckpoint:
    """SAVEPOINT-based checkpoint/rollback."""

    def test_checkpoint_create_and_release(self, store: SynapsisStore) -> None:
        """Create and release a checkpoint."""
        result = store.checkpoint_create("test_cp")
        assert result["name"] == "test_cp"
        assert "created_at" in result

        result = store.checkpoint_release("test_cp")
        assert result["name"] == "test_cp"
        assert "released_at" in result

    def test_checkpoint_restore_rolls_back(self, store: SynapsisStore) -> None:
        """Restore should roll back changes made after checkpoint."""
        # Create a session
        session = store.create_session(topic="Before checkpoint")
        session_id = session["id"]

        # Create checkpoint
        store.checkpoint_create("rollback_test")

        # Make a change WITHOUT committing (commit would release savepoint in Python sqlite3)
        store._conn.execute(
            "UPDATE sessions SET topic = ? WHERE id = ?",
            ("After checkpoint — should be rolled back", session_id),
        )
        # NO commit here — savepoints are released by commit in Python sqlite3

        # Verify change is visible within this connection
        updated_row = store._conn.execute(
            "SELECT topic FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        assert updated_row is not None
        assert updated_row[0] == "After checkpoint — should be rolled back"

        # Restore checkpoint — should undo the change
        result = store.checkpoint_restore("rollback_test")
        assert result["success"] is True, f"Restore failed: {result.get('error', '')}"

        # Verify rollback
        rolled_back = store.get_session(session_id)
        assert rolled_back is not None
        assert rolled_back["topic"] == "Before checkpoint"

    def test_checkpoint_restore_nonexistent(self, store: SynapsisStore) -> None:
        """Restoring a nonexistent checkpoint should return success=False."""
        result = store.checkpoint_restore("does_not_exist")
        assert result["success"] is False
        assert "error" in result

    def test_checkpoint_release_nonexistent(self, store: SynapsisStore) -> None:
        """Releasing a nonexistent checkpoint should return an error."""
        result = store.checkpoint_release("does_not_exist")
        assert "error" in result


# ===================================================================
# 3. safe_execute
# ===================================================================


class TestSafeExecute:
    """Atomic batch execution with auto-rollback."""

    def test_safe_execute_success(self, store: SynapsisStore) -> None:
        """All operations succeed — checkpoint is released."""
        session = store.create_session(topic="safe_exec_test")
        session_id = session["id"]

        def op1() -> None:
            store._conn.execute(
                "UPDATE sessions SET topic = ? WHERE id = ?",
                ("Updated via safe_execute", session_id),
            )
            store._conn.commit()

        def op2() -> None:
            store.add_observation(
                session_id=session_id,
                type="note",
                content="Observation from safe_execute",
            )

        result = store.safe_execute([op1, op2])
        assert result["success"] is True
        assert result["operations_completed"] == 2
        assert result["rolled_back"] is False

        # Verify both operations persisted
        updated = store.get_session(session_id)
        assert updated is not None
        assert updated["topic"] == "Updated via safe_execute"

    def test_safe_execute_rollback_on_failure(self, store: SynapsisStore) -> None:
        """Second operation fails — first should be rolled back."""
        session = store.create_session(topic="Before safe_execute")
        session_id = session["id"]

        def op1() -> None:
            # IMPORTANT: do NOT call commit() inside ops — it releases the savepoint
            store._conn.execute(
                "UPDATE sessions SET topic = ? WHERE id = ?",
                ("Should be rolled back", session_id),
            )

        def op2() -> None:
            msg = "Intentional failure"
            raise RuntimeError(msg)

        result = store.safe_execute([op1, op2])
        assert result["success"] is False
        assert result["operations_completed"] == 1
        assert result["rolled_back"] is True
        assert "Intentional failure" in result["error"]

        # Verify rollback: session topic should be unchanged
        rolled_back = store.get_session(session_id)
        assert rolled_back is not None
        assert rolled_back["topic"] == "Before safe_execute"


# ===================================================================
# 4. compute_health_score
# ===================================================================


class TestHealthScore:
    """Composite database health score (0-100)."""

    def test_healthy_db_scores_high(self, store: SynapsisStore) -> None:
        """Fresh DB should score 100 (no violations, no orphans)."""
        # Create minimal data
        store.create_session(topic="Health test")
        store.create_task("T-HEALTH-001", "Health test task", status="completed")

        result = store.compute_health_score()
        assert "score" in result
        assert "breakdown" in result
        assert "details" in result

        # Fresh DB should be healthy — score >= 85
        assert result["score"] >= 85, f"Expected high score, got {result['score']}"
        # integrity is a dict with penalty/message
        assert result["breakdown"]["integrity"]["penalty"] == 0
        # fk_violations is a dict with penalty/count
        assert result["breakdown"]["fk_violations"]["count"] == 0


# ===================================================================
# 5. ADD-only soft-supersede
# ===================================================================


class TestAddOnlySupersede:
    """ADD-only soft-supersede pattern for update_task."""

    def test_update_task_creates_new_version(self, store: SynapsisStore) -> None:
        """update_task should create a versioned new task and mark old as inactive."""
        store.create_task(
            "T-SUPER-001",
            "Original task",
            status="pending",
            priority="medium",
        )

        # Verify original is active
        original = store.get_task("T-SUPER-001")
        assert original is not None
        assert original["is_active"] is True

        # Update via soft-supersede
        new_id = store.update_task("T-SUPER-001", description="Updated description")
        assert new_id == "T-SUPER-001-v2"

        # Original should now be inactive
        original_again = store.get_task("T-SUPER-001", include_inactive=True)
        assert original_again is not None
        assert original_again["is_active"] is False
        assert original_again["superseded_by"] == "T-SUPER-001-v2"

        # New version should be active
        new_version = store.get_task("T-SUPER-001-v2")
        assert new_version is not None
        assert new_version["is_active"] is True
        assert new_version["description"] == "Updated description"

        # Default get_tasks should only return active
        tasks = store.get_tasks()
        active_ids = [t["id"] for t in tasks]
        assert "T-SUPER-001" not in active_ids
        assert "T-SUPER-001-v2" in active_ids

    def test_update_task_consecutive_versions(self, store: SynapsisStore) -> None:
        """Multiple updates should increment version."""
        store.create_task("T-VER-001", "Version test")
        v2 = store.update_task("T-VER-001", description="v2")
        assert v2 == "T-VER-001-v2"
        v3 = store.update_task(v2, description="v3")
        assert v3 == "T-VER-001-v3"

        v3_task = store.get_task("T-VER-001-v3")
        assert v3_task is not None
        assert v3_task["description"] == "v3"

        # Chain verification
        v2_task = store.get_task("T-VER-001-v2", include_inactive=True)
        assert v2_task is not None
        assert v2_task["is_active"] is False
        assert v2_task["superseded_by"] == "T-VER-001-v3"

    def test_update_task_not_found(self, store: SynapsisStore) -> None:
        """Updating a nonexistent task should return None."""
        result = store.update_task("T-NOPE-001", status="completed")
        assert result is None

    def test_update_task_no_updates(self, store: SynapsisStore) -> None:
        """No valid updates should return the task ID unchanged."""
        store.create_task("T-NOOP-001", "No-op test")
        result = store.update_task("T-NOOP-001")
        assert result == "T-NOOP-001"

    def test_get_task_include_inactive(self, store: SynapsisStore) -> None:
        """get_task with include_inactive=True should return inactive tasks."""
        store.create_task("T-INACT-001", "Will be superseded")
        store.update_task("T-INACT-001", status="completed")

        # Default: should not find inactive
        assert store.get_task("T-INACT-001") is None

        # With include_inactive: should find
        inactive = store.get_task("T-INACT-001", include_inactive=True)
        assert inactive is not None
        assert inactive["is_active"] is False


# ===================================================================
# 6. orphan_scan
# ===================================================================


class TestOrphanScan:
    """Detect stale in_progress tasks."""

    def test_orphan_scan_clean_db(self, store: SynapsisStore) -> None:
        """Fresh DB with no tasks should return empty list."""
        orphans = store.orphan_scan()
        assert isinstance(orphans, list)
        assert len(orphans) == 0

    def test_orphan_scan_finds_stale_task(self, store: SynapsisStore) -> None:
        """An in_progress task >24h old with no handoff_refs should be detected."""
        from datetime import UTC, datetime, timedelta

        old_ts = (datetime.now(UTC) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")

        # Manually insert a stale in_progress task
        store._conn.execute(
            """INSERT INTO tasks
               (id, description, status, priority, owner, tags, parent,
                handoff_refs, compression_level, created_at, updated_at,
                is_active, superseded_by)
               VALUES (?, ?, 'in_progress', 'medium', 'efesto', '[]', NULL,
                       '[]', 0, ?, ?, 1, NULL)""",
            ("T-STALE-001", "Stale task", old_ts, old_ts),
        )
        store._conn.commit()

        orphans = store.orphan_scan()
        assert len(orphans) == 1
        assert orphans[0]["task_id"] == "T-STALE-001"
        # Should mention missing handoff_refs and events (stale task)
        assert "no handoff_refs" in orphans[0]["reason"].lower()
        assert "no recent events" in orphans[0]["reason"].lower()

    def test_orphan_scan_skips_recent_task(self, store: SynapsisStore) -> None:
        """A recent in_progress task should NOT be flagged as orphan."""
        from datetime import UTC, datetime, timedelta

        recent_ts = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")

        store._conn.execute(
            """INSERT INTO tasks
               (id, description, status, priority, owner, tags, parent,
                handoff_refs, compression_level, created_at, updated_at,
                is_active, superseded_by)
               VALUES (?, ?, 'in_progress', 'medium', 'efesto', '[]', NULL,
                       '[]', 0, ?, ?, 1, NULL)""",
            ("T-FRESH-001", "Recent task", recent_ts, recent_ts),
        )
        store._conn.commit()

        orphans = store.orphan_scan()
        fresh_orphans = [o for o in orphans if o["task_id"] == "T-FRESH-001"]
        assert len(fresh_orphans) == 0


# ===================================================================
# 7. db_set_pragma
# ===================================================================


class TestDbSetPragma:
    """Runtime PRAGMA configuration."""

    def test_set_busy_timeout(self, store: SynapsisStore) -> None:
        """Setting busy_timeout should succeed."""
        result = store.db_set_pragma("busy_timeout", 10000)
        assert result["pragma"] == "busy_timeout"
        assert result["value"] == 10000
        assert "set_at" in result

    def test_set_fullfsync(self, store: SynapsisStore) -> None:
        """Setting fullfsync should succeed."""
        result = store.db_set_pragma("fullfsync", 1)
        assert result["pragma"] == "fullfsync"
        # Verify the PRAGMA was set
        row = store._conn.execute("PRAGMA fullfsync").fetchone()
        assert row is not None
        assert int(row[0]) == 1
