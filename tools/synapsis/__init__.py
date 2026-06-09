"""Synapsis — Unified memory layer for Team Olimpo.

Fuses session_memory (6 tools) + taskmanager (7 tools) into a single
cross-agent MCP server with SQLite backing store, full-text search,
entity linking, domain-gating, and unified compression.

Provides 21 tools (13 legacy-compatible + 6 Chimera + 2 admin):

Legacy (session_* — gated by domain ``session``):
    - session_init — Initialize or resume a session (3-layer context)
    - session_observe — Log an observation to the timeline
    - session_context — Retrieve progressive context (3-layer disclosure)
    - session_recall — FTS5 search across sessions with entity/type filters
    - session_summarize — Compress observations into a summary
    - session_compress — Compress old observations (hot/warm/cold)

Legacy (task_* — gated by domain ``task``):
    - task_create — Create a new task
    - task_update_status — Transition a task's status (state machine)
    - task_query — Search and filter tasks
    - task_summary — Aggregate task statistics
    - task_log_event — Append an event to a task's audit log
    - task_export — Export all state as YAML
    - taskmanager_compress — Compress task event logs (hot/warm/cold)

Chimera (6 — gated by ``system`` / ``entity``):
- context → Progressive disclosure context (claude-mem)
    - consolidate → Sleep-cycle consolidation (Engram + linksee)
    - cross_reference → Entity-linked cross-reference (Mem0)
    - entity_search → Multi-signal entity search (Mem0)
    - timeline → Unified chronological timeline
    - stats → Enhanced aggregate statistics

Admin (2):
    - domain_toggle → Enable/disable domains at runtime
    - vacuum → VACUUM + index reorganisation
"""

__version__ = "0.4.0"
