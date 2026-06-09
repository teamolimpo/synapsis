"""Data models for Synapsis unified memory layer.

Provides enums for status/type classification, dataclasses for serialization,
and helper functions for ID generation and timestamps.

Combines models from:
    - tools/session_memory/models.py (SessionData, ObservationData, EntityData, SummaryData)
    - tools/taskmanager/models.py (Task, TaskEvent, StateMachine)

Exports:
    SessionStatus, ObservationType, EntityType, TaskStatus, Priority, EventType — enums
    SessionData, ObservationData, EntityData, SummaryData — session dataclasses
    TaskData, TaskEventData — task dataclasses
    DomainData, CounterData, MemoryLayerData — new Synapsis dataclasses
    generate_session_id, generate_task_id, now_iso — helpers
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SessionStatus(StrEnum):
    """Lifecycle status of a Poros session."""

    ACTIVE = "active"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"


class ObservationType(StrEnum):
    """Classification of a single observation in the timeline.

    Short canonical forms for token efficiency on frequent observe calls.
    """

    DECISION = "dec"
    DELEGATION = "del"
    RESULT = "res"
    NOTE = "note"
    HANDOFF = "hf"
    USER_MESSAGE = "um"
    SYSTEM = "sys"


class EntityType(StrEnum):
    """Classification of a named entity."""

    PROJECT = "project"
    AGENT = "agent"
    CONCEPT = "concept"
    PERSON = "person"
    TECHNOLOGY = "technology"
    TASK = "task"


class TaskStatus(StrEnum):
    """Lifecycle status of a task (from taskmanager).

    Short canonical forms for token efficiency on frequent updates/queries.
    Long forms are still accepted on input for backward compat (normalized internally).
    """

    PENDING = "pend"
    IN_PROGRESS = "prog"
    COMPLETED = "done"
    CANCELLED = "x"
    BLOCKED = "blk"
    STANDBY = "stby"


class Priority(StrEnum):
    """Task priority levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EventType(StrEnum):
    """Types of task events.

    Short canonical forms for token efficiency (especially "hr" for every handoff log).
    """

    HANDOFF_REF = "hr"
    NOTE = "note"
    DECISION = "dec"
    DEVIATION = "dv"
    STATUS_CHANGE = "sc"
    CREATED = "cr"


class DomainId(StrEnum):
    """System domain identifiers for domain-gating."""

    SESSION = "session"
    TASK = "task"
    SYSTEM = "system"
    ENTITY = "entity"


class MemoryLayer(StrEnum):
    """Memory layers for structured memory (from linksee-memory pattern)."""

    GOAL = "goal"
    CONTEXT = "context"
    EMOTION = "emotion"
    IMPLEMENTATION = "implementation"
    CAVEAT = "caveat"
    LEARNING = "learning"


# ---------------------------------------------------------------------------
# State machine constants (from taskmanager/models.py)
# ---------------------------------------------------------------------------

VALID_STATUSES = [s.value for s in TaskStatus]
VALID_PRIORITIES = [p.value for p in Priority]
VALID_EVENT_TYPES = [
    e.value for e in EventType if e not in (EventType.STATUS_CHANGE, EventType.CREATED)
]
INITIAL_STATUSES = [TaskStatus.PENDING.value, TaskStatus.STANDBY.value]


def _short_status(s: str) -> str:
    """Normalize long or short status to the current canonical short form."""
    if not s:
        return s
    v = s.lower().strip()
    m = {
        "pending": "pend", "pend": "pend", "p": "pend",
        "in_progress": "prog", "prog": "prog", "ip": "prog",
        "completed": "done", "done": "done", "d": "done",
        "cancelled": "x", "x": "x", "cancel": "x",
        "blocked": "blk", "blk": "blk", "b": "blk",
        "standby": "stby", "stby": "stby", "s": "stby",
    }
    return m.get(v, v)

TASK_ID_REGEX = re.compile(r"^T-[A-Z][A-Z0-9-]{1,14}-\d{3}$")


class StateMachine:
    """Validates task status transitions.

    Transition matrix (from -> to) - using short canonical forms:

        | From - To | pend | prog | done | x | blk | stby |
        |-----------|------|------|------|---|-----|------|
        | pend      | -    | X    | X    | X | X   | X    |
        | prog      | -    | -    | X    | X | X   | -    |
        | done      | -    | -    | -    | - | -   | -    |
        | x         | -    | -    | -    | - | -   | -    |
        | blk       | -    | X    | X    | X | -   | -    |
        | stby      | X    | X    | X    | X | X   | -    |

    ``completed`` and ``cancelled`` are terminal states.
    """

    _TRANSITIONS: dict[str, set[str]] = {
        "pend": {"prog", "done", "x", "blk", "stby"},
        "prog": {"done", "x", "blk"},
        "done": set(),
        "x": set(),
        "blk": {"prog", "done", "x"},
        "stby": {"pend", "prog", "done", "x", "blk"},
    }

    @classmethod
    def is_valid_transition(cls, from_status: str, to_status: str) -> bool:
        """Check if a status transition is allowed (accepts long or short forms)."""
        fs = _short_status(from_status)
        ts = _short_status(to_status)
        if fs not in cls._TRANSITIONS:
            return False
        return ts in cls._TRANSITIONS[fs]

    @classmethod
    def valid_transitions(cls, from_status: str) -> list[str]:
        """Return list of valid target statuses from a given status."""
        fs = _short_status(from_status)
        return sorted(cls._TRANSITIONS.get(fs, []))

    @classmethod
    def validate_transition(cls, from_status: str, to_status: str) -> None:
        """Validate a transition. Raises ``ValueError`` if invalid.

        Args:
            from_status: Current status.
            to_status: Desired new status.

        Raises:
            ValueError: With a descriptive message if the transition is invalid.
        """
        if from_status == to_status:
            return
        if not cls.is_valid_transition(from_status, to_status):
            valid = cls.valid_transitions(from_status)
            if not valid:
                raise ValueError(
                    f"Invalid transition '{from_status}' → '{to_status}'. "
                    f"'{from_status}' is a terminal state. "
                    f"Allowed transitions: (none)."
                )
            raise ValueError(
                f"Invalid transition '{from_status}' → '{to_status}'. "
                f"Allowed transitions from '{from_status}': "
                f"{', '.join(valid)}."
            )


# ---------------------------------------------------------------------------
# ID helpers (from taskmanager/models.py + session_memory/models.py)
# ---------------------------------------------------------------------------


def extract_area_from_description(description: str) -> str:
    """Extract an area code from a task description for ID generation.

    Strategy:
    1. Look for acronyms in ALL CAPS (e.g. ``MCP``, ``API``, ``CLI``).
    2. If none, take the first alphabetic word and uppercase it.
    3. Fall back to ``TASK``.

    Returns:
        Area string (max 10 characters, uppercase).
    """
    words = description.split()
    for w in words:
        w_clean = w.strip("()[]{}:;,.")
        if w_clean.isupper() and len(w_clean) >= 2 and w_clean.isalpha():
            return w_clean[:10]
    for w in words:
        w_clean = w.strip("()[]{}:;,.")
        if len(w_clean) >= 2 and w_clean.isalpha():
            return w_clean[:10].upper()
    return "TASK"


def extract_area_from_task_id(task_id: str) -> str:
    """Extract the area portion from a task ID.

    Example: ``T-MCP-ROADMAP-001`` → ``MCP-ROADMAP``.
    """
    m = re.match(r"^T-(.+)-(\d{3})$", task_id)
    if m:
        return m.group(1)
    return "TASK"


def validate_task_id(task_id: str) -> bool:
    """Check if a task ID matches the ``T-<AREA>-<NNN>`` format."""
    return bool(TASK_ID_REGEX.match(task_id))


# ---------------------------------------------------------------------------
# Dataclasses — Session domain
# ---------------------------------------------------------------------------


@dataclass
class DomainData:
    """A system domain — one row in the ``domains`` table."""

    id: str
    description: str = ""
    is_active: bool = True
    created_at: str = ""


@dataclass
class SessionData:
    """A Poros session — one row in the ``sessions`` table."""

    id: str
    status: str = SessionStatus.ACTIVE.value
    topic: str = ""
    summary: str = ""
    agent: str = "Poros"
    task_ids: list[str] = field(default_factory=list)
    token_budget: int = 2000
    token_discovery: int = 0
    token_read: int = 0
    started_at: str = ""
    ended_at: str | None = None
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ObservationData:
    """A single observation — one row in the ``observations`` table."""

    id: int | None = None
    session_id: str = ""
    parent_id: int | None = None
    type: str = ObservationType.NOTE.value
    agent: str = "Poros"
    content: str = ""
    tokens_discovery: int = 0
    tokens_read: int = 0
    token_savings: float = 0.0
    entities: list[str] = field(default_factory=list)
    handoff_path: str | None = None
    task_ref: str | None = None
    compression_level: int = 0
    created_at: str = ""


@dataclass
class EntityData:
    """A named entity — one row in the ``entities`` table."""

    id: int | None = None
    name: str = ""
    entity_type: str = EntityType.CONCEPT.value
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class SummaryData:
    """A compressed summary — one row in the ``summaries`` table."""

    id: int | None = None
    session_id: str = ""
    level: int = 1
    parent_id: int | None = None
    content: str = ""
    token_count: int = 0
    created_at: str = ""


@dataclass
class TaskEventData:
    """A single event in a task's audit log — one row in ``task_events``."""

    id: int | None = None
    task_id: str = ""
    type: str = EventType.NOTE.value
    details: str = ""
    handoff_path: str | None = None
    compression_level: int = 0
    created_at: str = ""


@dataclass
class TaskData:
    """A task in the Team Olimpo workflow — one row in ``tasks`` table."""

    id: str
    description: str
    status: str = TaskStatus.PENDING.value
    priority: str = Priority.MEDIUM.value
    owner: str = "Poros"
    tags: list[str] = field(default_factory=list)
    parent: str | None = None
    handoff_refs: list[str] = field(default_factory=list)
    compression_level: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class CounterData:
    """An area counter for task ID generation."""

    area: str
    last_value: int = 0


@dataclass
class MemoryLayerData:
    """A structured memory layer (linksee-memory pattern)."""

    id: int | None = None
    session_id: str = ""
    layer: str = MemoryLayer.CONTEXT.value
    content: str = ""
    source_observation_id: int | None = None
    forgetting_risk: float = 0.0
    created_at: str = ""
    updated_at: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def generate_session_id() -> str:
    """Generate a unique session ID in format ``ses_YYYYMMDD_HHMMSS_ffffff``.

    Includes microseconds to guarantee uniqueness within the same second.

    Returns:
        Session ID string (e.g. ``ses_20260523_143000_123456``).
    """
    return datetime.now(UTC).strftime("ses_%Y%m%d_%H%M%S_%f")


def generate_task_id(area: str, last_value: int) -> str:
    """Generate a task ID in format ``T-<AREA>-<NNN>``.

    Args:
        area: Area string (e.g. ``MCP``).
        last_value: Current counter value.

    Returns:
        Task ID string (e.g. ``T-MCP-001``).
    """
    return f"T-{area}-{last_value + 1:03d}"


def now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format with microseconds.

    Returns:
        ISO 8601 timestamp string (e.g. ``2026-05-23T14:30:00.123456``).
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")


def now_iso_seconds() -> str:
    """Return current UTC timestamp in ISO 8601 format (seconds precision).

    Returns:
        ISO 8601 timestamp string (e.g. ``2026-05-23T14:30:00``).
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def compute_token_savings(tokens_discovery: int, tokens_read: int) -> float:
    """Compute the token savings ratio.

    Args:
        tokens_discovery: Token count for generating the observation.
        tokens_read: Token count for reading the observation.

    Returns:
        Savings ratio (0.0–1.0), or 0.0 if tokens_discovery is 0.
    """
    if tokens_discovery > 0:
        return (tokens_discovery - tokens_read) * 1.0 / tokens_discovery
    return 0.0


# ---------------------------------------------------------------------------
# Additional helpers (from taskmanager/models.py — for tool compatibility)
# ---------------------------------------------------------------------------


def truncate_description(description: str, max_len: int = 150) -> tuple[str, bool]:
    """Truncate description to max_len characters.

    Returns:
        Tuple of (truncated_description, was_truncated).
    """
    if len(description) <= max_len:
        return description, False
    return description[:max_len], True


def validate_priority(priority: str) -> None:
    """Raise ``ValueError`` if priority is not valid."""
    if priority not in VALID_PRIORITIES:
        raise ValueError(
            f"priority must be one of: {', '.join(VALID_PRIORITIES)}. Got: '{priority}'."
        )


def validate_status(status: str) -> None:
    """Raise ``ValueError`` if status is not valid."""
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(VALID_STATUSES)}. Got: '{status}'.")


def validate_event_type(event_type: str) -> None:
    """Raise ``ValueError`` if event_type is not valid."""
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(
            f"event_type must be one of: {', '.join(VALID_EVENT_TYPES)}. Got: '{event_type}'."
        )


# ---------------------------------------------------------------------------
# Normalization helpers (token discipline — short canonical forms)
# These accept long or short (and aliases) on input and return the canonical short.
# Used by store (for direct + test callers) and server MCP layer.
# ---------------------------------------------------------------------------

def normalize_task_status(val: str | None) -> str | None:
    """Normalize to canonical short task status (pend/prog/done/x/blk/stby)."""
    if not val:
        return val
    v = val.lower().strip()
    m = {
        "pending": "pend", "pend": "pend", "p": "pend",
        "in_progress": "prog", "prog": "prog", "ip": "prog",
        "completed": "done", "done": "done", "d": "done",
        "cancelled": "x", "x": "x", "cancel": "x",
        "blocked": "blk", "blk": "blk", "b": "blk",
        "standby": "stby", "stby": "stby", "s": "stby",
    }
    return m.get(v, v)


def normalize_event_type(val: str | None) -> str | None:
    """Normalize to canonical short event type (hr/note/dec/dv/sc/cr)."""
    if not val:
        return val
    v = val.lower().strip()
    m = {
        "handoff_ref": "hr", "hr": "hr", "handoff": "hr",
        "note": "note", "n": "note",
        "decision": "dec", "dec": "dec", "d": "dec",
        "deviation": "dv", "dv": "dv",
        "status_change": "sc", "sc": "sc",
        "created": "cr", "cr": "cr",
    }
    return m.get(v, v)


def normalize_observe_type(val: str | None) -> str | None:
    """Normalize to canonical short observe type (dec/del/res/note/hf/um/sys)."""
    if not val:
        return val
    v = val.lower().strip()
    m = {
        "decision": "dec", "dec": "dec",
        "delegation": "del", "del": "del",
        "result": "res", "res": "res",
        "note": "note", "n": "note",
        "handoff": "hf", "hf": "hf",
        "user_message": "um", "um": "um",
        "system": "sys", "sys": "sys",
    }
    return m.get(v, v)
