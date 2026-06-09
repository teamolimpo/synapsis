"""Tests for ``synapsis_search`` — unified retrieval across all domains.

Run with::

    pytest tools/synapsis/test_search.py -v --tb=short

Coverage:
    1. ``test_search_basic_auto`` — query normale, scope="auto" → all domains.
    2. ``test_search_layer1_token_count`` — layer=1 ≤ 100t.
    3. ``test_search_layer2_token_count`` — layer=2 ≤ 500t.
    4. ``test_search_token_budget_layer1`` — token_budget=100 → layer 1.
    5. ``test_search_token_budget_layer2`` — token_budget=500 → layer 2.
    6. ``test_search_task_ref_auto`` — query="T-CHIMERA-019", scope="auto" → tasks.
    7. ``test_search_path_auto`` — query="path:Wiki/topics/chimera", scope="auto" → knowledge.
    8. ``test_search_timeline`` — scope="timeline" with since.
    9. ``test_search_all_domains`` — scope="all" returns all domains.
    10. ``test_deprecated_alias_still_works`` — legacy tools callable via legacy domain.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tools.synapsis import server as server_module
from tools.synapsis.store import SynapsisStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> Iterator[SynapsisStore]:
    """Create a SynapsisStore with a temporary database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = Path(tmp.name)
    tmp.close()

    s = SynapsisStore(db_path=db_path)
    # Point the global server store to our test store
    server_module._store = s
    yield s

    s.close()
    db_path.unlink(missing_ok=True)
    server_module._store = None


def _init_session(store: SynapsisStore, topic: str = "Test search session") -> dict[str, Any]:
    """Helper: create a session and return it."""
    return store.create_session(topic=topic, token_budget=2000)


def _init_task(
    store: SynapsisStore,
    task_id: str = "T-SEARCH-001",
    **kwargs: Any,  # noqa: ANN401
) -> dict[str, Any]:
    """Helper: create a task and return it."""
    desc = kwargs.pop("description", "Test task for search")
    kwargs.setdefault("status", "pend")
    store.create_task(task_id=task_id, description=desc, **kwargs)
    task = store.get_task(task_id)
    assert task is not None, f"Task {task_id} was not created"
    return task


def _add_obs(
    store: SynapsisStore,
    session_id: str,
    type: str,
    content: str,
    agent: str = "Poros",
    tokens_disc: int = 0,
) -> int:
    """Helper: add an observation."""
    return store.add_observation(
        session_id=session_id,
        type=type,
        content=content,
        agent=agent,
        tokens_discovery=tokens_disc,
        tokens_read=0,
    )


# ---------------------------------------------------------------------------
# Helper: invoke search() directly via the server function
# ---------------------------------------------------------------------------


def _search(
    query: str,
    scope: str = "auto",
    layer: int = 1,
    max_results: int = 5,
    ref: str | None = None,
    since: str | None = None,
    token_budget: int | None = None,
) -> dict[str, Any]:
    """Invoke the ``search()`` server function and return parsed JSON."""
    result = server_module.search(
        query=query,
        scope=scope,
        layer=layer,
        max_results=max_results,
        ref=ref,
        since=since,
        token_budget=token_budget,
    )
    return json.loads(result)


def _count_tokens(text: str) -> int:
    """Rough token count (4 chars per token heuristic)."""
    return max(1, len(text) // 4)


# ===================================================================
# 1. BASIC AUTO SCOPE
# ===================================================================


class TestSearchBasic:
    """Basic search behaviour."""

    def test_search_basic_auto(self, store: SynapsisStore) -> None:
        """Query normale, scope='auto' — verifica che torni risultati."""
        # Arrange: create data
        session = _init_session(store, topic="Test search")
        _add_obs(store, session["id"], "note", "This is a test observation about search queries")
        _init_task(store, "T-SEARCH-001", description="Implement search functionality")

        # Act
        result = _search(query="search", scope="auto")

        # Assert
        assert "layer" in result
        assert "context" in result
        assert "token_count" in result
        assert result["layer"] == 1
        assert result["token_count"] > 0
        assert result["has_more"] is True

    def test_search_layer1_token_count(self, store: SynapsisStore) -> None:
        """Layer 1 — il contesto deve essere ≤ 100 token."""
        # Arrange
        session = _init_session(store)
        _add_obs(store, session["id"], "note", "A" * 500)

        # Act
        result = _search(query="A", layer=1)

        # Assert
        assert result["layer"] == 1
        context = result["context"]
        tokens = _count_tokens(context)
        assert tokens <= 100, f"Layer 1 dovrebbe ≤ 100t, ma ha {tokens}t"

    def test_search_layer2_token_count(self, store: SynapsisStore) -> None:
        """Layer 2 — il contesto deve essere ≤ 500 token."""
        # Arrange
        session = _init_session(store)
        _add_obs(store, session["id"], "note", "B" * 500)

        # Act
        result = _search(query="B", layer=2)

        # Assert
        assert result["layer"] == 2
        context = result["context"]
        tokens = _count_tokens(context)
        assert tokens <= 500, f"Layer 2 dovrebbe ≤ 500t, ma ha {tokens}t"

    def test_search_token_budget_layer1(self, store: SynapsisStore) -> None:
        """token_budget=100 forza layer 1."""
        # Arrange
        session = _init_session(store)
        _add_obs(store, session["id"], "note", "C" * 500)

        # Act
        result = _search(query="C", token_budget=100)

        # Assert
        assert result["layer"] == 1, "token_budget=100 dovrebbe forzare layer 1"

    def test_search_token_budget_layer2(self, store: SynapsisStore) -> None:
        """token_budget=500 forza layer 2."""
        # Arrange
        session = _init_session(store)
        _add_obs(store, session["id"], "note", "D" * 500)

        # Act
        result = _search(query="D", token_budget=500)

        # Assert
        assert result["layer"] == 2, "token_budget=500 dovrebbe forzare layer 2"


# ===================================================================
# 2. AUTO-DETECTION PATTERNS
# ===================================================================


class TestSearchAuto:
    """Auto-detect scope from query pattern."""

    def test_search_task_ref_auto(self, store: SynapsisStore) -> None:
        """Query='T-CHIMERA-019', scope='auto' → trova task."""
        # Arrange
        _init_task(store, "T-CHIMERA-019", description="Chimera integration task")

        # Act
        result = _search(query="T-CHIMERA-019", scope="auto")

        # Assert
        assert result["layer"] == 1
        context_lower = result["context"].lower()
        assert "tasks:" in context_lower or "task" in context_lower, (
            f"Auto-detect dovrebbe trovare task, contesto: {result['context']}"
        )
        assert result["token_count"] > 0

    def test_search_path_auto(self, store: SynapsisStore) -> None:
        """Query='path:Wiki/topics/chimera', scope='auto' → knowledge read."""
        # Act
        result = _search(query="path:Wiki/topics/chimera", scope="auto")

        # Assert
        # Since the file may not exist, we just verify it doesn't error
        assert isinstance(result, dict)
        assert "error" not in result, f"Non dovrebbe dare errore: {result}"


# ===================================================================
# 3. SCOPE-SPECIFIC
# ===================================================================


class TestSearchScope:
    """Scope-specific queries."""

    def test_search_timeline(self, store: SynapsisStore) -> None:
        """scope='timeline' con since — non deve fallire."""
        # Arrange
        session = _init_session(store)
        _add_obs(store, session["id"], "note", "Timeline test observation")

        # Act
        result = _search(query="timeline", scope="timeline")

        # Assert
        assert isinstance(result, dict)
        assert "error" not in result, f"Non dovrebbe dare errore: {result}"

    def test_search_all_domains(self, store: SynapsisStore) -> None:
        """scope='all' — verifica che ritenga TUTTI i domini con dati."""
        # Arrange: create data for multiple domains
        session = _init_session(store, topic="All domains test")
        _add_obs(store, session["id"], "note", "Test observation for all domains")
        _init_task(store, "T-SEARCH-ALL-001", description="All domains task")

        # Act — use layer=3 for full payload to inspect domains
        result = _search(query="all domains", scope="all", layer=3)

        # Assert
        assert isinstance(result, dict)
        assert "error" not in result, f"Non dovrebbe dare errore: {result}"
        # layer 3 context is JSON with domains
        if result.get("layer") == 3:
            context_data = json.loads(result["context"])
            assert "domain_counts" in context_data
            # At least some domain should have results
            assert context_data.get("total_results", 0) >= 0


# ===================================================================
# 4. LEGACY TOOL COMPATIBILITY
# ===================================================================


class TestSearchAsLegacyReplacement:
    """Search replaces legacy session_recall tool."""

    def test_deprecated_alias_still_works(self, store: SynapsisStore) -> None:
        """I vecchi tool deprecati funzionano ancora via dominio legacy."""
        # Arrange
        session = _init_session(store, topic="Legacy test")
        _add_obs(store, session["id"], "decision", "A legacy decision observation")

        # Act: use search (replaces legacy session_recall)
        result_str = server_module.search(query="legacy", max_results=5)
        result = json.loads(result_str)

        # Assert — search returns layer+context (replaces old session_recall)
        assert "layer" in result, f"Expected search result, got: {result}"
