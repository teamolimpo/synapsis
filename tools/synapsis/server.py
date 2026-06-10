"""Synapsis MCP server. 8 tools: search, session, task, admin, consolidate, hf, d_set, d_get.

Run: uv run python -m tools.synapsis.server
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

# ---------------------------------------------------------------------------
# MCP SDK — graceful fallback if missing
# ---------------------------------------------------------------------------

try:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("synapsis")
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

# ---------------------------------------------------------------------------
# Backing store (singleton-like, created on first use)
# ---------------------------------------------------------------------------

_store: Any = None  # SynapsisStore


def _get_store() -> Any:
    global _store
    if _store is None:
        from tools.synapsis.store import SynapsisStore

        _store = SynapsisStore()
    return _store


# -------------------------------------------------------------------
# JSON key compression: shorter keys = fewer tokens per response
# -------------------------------------------------------------------

_JMAP = {
    # consolidate
    "consolidated": "n",
    "patterns_detected": "pat",
    "contradictions_detected": "ct",
    "memory_layers_created": "ml",
    "sessions_affected": "sess",
    "dry_run": "dry",
    "top_entities": "top",
    "contradictions": "cts",
    # session init
    "observations_count": "obs",
    "token_savings_avg": "sav",
    "session_id": "sid",
    "is_resumed": "resumed",
    "token_budget": "budget",
    # session context
    "token_count": "tokens",
    "has_more": "more",
    "suggestion": "hint",
    # session summarize
    "observations_compressed": "obs",
    "token_savings": "saved",
    "summary_id": "sid",
    # session compress
    "observations_warm": "owa",
    "summaries_created": "sum",
    "events_warm": "ewa",
    "events_cold": "eco",
    "tasks_processed": "tasks",
    # admin
    "violations_found": "vio",
    "health_score": "hscore",
    "orphan_tasks": "orphans",
    "total_chunks": "chunks",
    "total_files": "files",
    "last_update": "last_upd",
    "db_size_kb": "size_kb",
    # task
    "auto_parent_completed": "parent_done",
    "by_status": "status",
    "by_priority": "priority",
    "wip_current": "wip",
    "oldest_pending": "oldest",
    "event_index": "idx",
    # search
    "total_results": "total",
    "domain_counts": "counts",
    # hf
    "observation_id": "oid",
    "entities_found": "ents",
    "session_updated": "updated",
    # Error responses
    "error": "e",
    "context": "ctx",
    "result": "res",
    "warning": "warn",
}

# Shared layer suggestions (used in search and session context)
_LAYER_SUGGESTIONS = {
    1: "Use layer=2 for details.",
    2: "Use layer=3 for full payload.",
    3: "Use specific scope (knowledge/tasks/observations) for targeted search.",
}

_LAYER_SUGGESTIONS_CROSS_SESSION = {
    **_LAYER_SUGGESTIONS,
    3: "Use search for cross-session.",
}


def _j(data: dict) -> str:
    """json.dumps with compressed keys (see _JMAP above)."""
    compressed = {_JMAP.get(k, k): v for k, v in data.items()}
    return json.dumps(compressed, ensure_ascii=False)


# -------------------------------------------------------------------
# Token discipline: short canonical forms for hot input paths
# (introduced for systematic token savings on repeated tool calls)
# Long forms accepted on input (normalized to short canonical on write/use).
# -------------------------------------------------------------------

def _norm_task_status(val: str | None) -> str | None:
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

def _norm_event_type(val: str | None) -> str | None:
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

def _norm_observe_type(val: str | None) -> str | None:
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

def _norm_task_act(val: str | None) -> str | None:
    if not val:
        return val
    v = val.lower().strip()
    m = {
        "create": "c", "c": "c",
        "query": "q", "q": "q",
        "update": "u", "u": "u",
        "log": "l", "l": "l",
        "summary": "sum", "sum": "sum", "s": "sum",
        "export": "exp", "exp": "exp", "e": "exp",
        "compress": "z", "z": "z", "cmp": "z",
    }
    return m.get(v, v)


# ===================================================================
#  8 MCP tools: search, session, task, admin, consolidate, hf
# ===================================================================


# -------------------------------------------------------------------
# Tool 1: search
# -------------------------------------------------------------------


@mcp.tool()
def search(
    query: str,
    scope: str = "auto",
    l: int = 1,
    n: int = 5,
    ref: str | None = None,
    since: str | None = None,
    tk: int | None = None,
) -> str:
    """Search knowledge, tasks, observations, entities, memory_layers, session, timeline, hf."""
    logger.info(
        f"search: query='{query[:80]}', scope={scope}, l={l}, "
        f"n={n}, ref={ref}, since={since}, tk={tk}"
    )

    if not query or not query.strip():
        return json.dumps({"error": "'query' required"})

    # Resolve layer from tk
    if tk is not None:
        if tk < 200:
            l = 1
        elif tk <= 1000:
            l = 2
        else:
            l = 3

    store = _get_store()
    results = store.unified_search(
        query=query.strip(),
        scope=scope,
        max_results=n,
        ref=ref,
        since=since,
    )

    domains = results.get("domains", {})
    counts = results.get("domain_counts", {})
    total = results.get("total_results", 0)

    # Build layer content
    if l == 1:
        lines = [f'SynapsisSearch: "{query.strip()}"']
        for domain_name in sorted(counts):
            lines.append(f"  {domain_name.title()}: {counts[domain_name]} results")
        lines.append("")
        if total > 0:
            lines.append(f"  Total: {total} results across {len(counts)} domains")
        else:
            lines.append("  (no results)")
        lines.append("")
        lines.append("(Use l=2 for details)")
        context = "\n".join(lines)

    elif l == 2:
        parts = [f'SynapsisSearch: "{query.strip()}"', ""]
        if "knowledge" in domains:
            parts.append(f" Knowledge ({counts['knowledge']}):")
            for k in domains["knowledge"][:3]:
                parts.append(f"   - {k['title']}: {k['snippet'][:150]}")
            parts.append("")
        if "tasks" in domains:
            parts.append(f" Tasks ({counts['tasks']}):")
            for t in domains["tasks"][:5]:
                desc = t.get("description", "")[:80]
                parts.append(f'   - {t["id"]} "{desc}" [{t.get("status", "?")}]')
            parts.append("")
        if "observations" in domains:
            parts.append(f" Observations ({counts['observations']}):")
            for o in domains["observations"][:5]:
                ts = (o.get("created_at") or "")[11:16]
                parts.append(f'   [{ts}] {o.get("agent", "?")}: "{o["snippet"][:100]}"')
            parts.append("")
        if "entities" in domains:
            parts.append(f" Entities ({counts['entities']}):")
            for e in domains["entities"][:5]:
                md = e.get("metadata", {}) or {}
                if md and isinstance(md, dict):
                    status = md.get("status")
                    phase = md.get("phase")
                    health = md.get("health")
                    state_hash = md.get("state_hash")
                    meta_parts_list = [
                        f"status: {status}" if status else None,
                        f"phase: {phase}" if phase else None,
                        f"health: {health}" if health else None,
                        f"state_hash: {state_hash}" if state_hash else None,
                    ]
                    meta_str = ", ".join(p for p in meta_parts_list if p)
                    if meta_str:
                        parts.append(f"   - {e['name']} ({e.get('type', '?')}) — {meta_str}")
                        continue
                parts.append(
                    f"   - {e['name']} ({e.get('type', '?')}) — {e.get('observation_count', 0)} obs"
                )
            parts.append("")
        if "session" in domains:
            parts.append(f" Sessions ({counts['session']}):")
            for s in domains["session"][:3]:
                parts.append(f"   - {s['id']}: {s.get('topic', '?')[:60]} [{s.get('status', '?')}]")
            parts.append("")
        if "timeline" in domains:
            parts.append(f" Timeline ({counts['timeline']}):")
            for tl in domains["timeline"][:5]:
                ts = (tl.get("created_at") or "")[5:19]
                parts.append(f"   [{ts}] {tl.get('snippet', '')[:120]}")
            parts.append("")
        if not domains:
            parts.append(" (no results)")
        context = "\n".join(parts)

    else:
        # Layer 3: full payload
        output: dict[str, Any] = {
            "query": query.strip(),
            "total_results": total,
            "domain_counts": counts,
            "domains": domains,
        }
        context = json.dumps(output, indent=2)

    token_count = _count_tokens(context)
    has_more = l < 3
    suggestions = _LAYER_SUGGESTIONS

    result = {
        "layer": l,
        "context": context,
        "token_count": token_count,
        "has_more": has_more,
        "suggestion": suggestions.get(l, ""),
    }

    return json.dumps(result)


# -------------------------------------------------------------------
# Tool 2: session  — session lifecycle (replaces 6 tools)
#
# action: init | observe | context | summarize | compress | tasks
# -------------------------------------------------------------------


@mcp.tool()
def session(
    act: str = "init",
    # Common
    sid: str | None = None,
    # Init-specific
    topic: str | None = None,
    tids: list[str] | None = None,
    resume: bool = True,
    tk: int = 2000,
    # Observe-specific
    type: str | None = None,
    content: str | None = None,
    agent: str = "Poros",
    entities: list[str] | None = None,
    hpath: str | None = None,
    tref: str | None = None,
    tkdc: int = 0,
    tkrd: int = 0,
    pid: int | None = None,
    # Context/Summarize-specific
    l: int = 1,
    force: bool = False,
    lv: int = 1,
    mtk: int | None = None,
    # Compress-specific
    days: int | None = None,
    ml: int = 2,
    dry: bool = True,
) -> str:
    """Session lifecycle: init | observe | context | summarize | compress | tasks."""
    # Support short act for token savings: i/o/ctx/sum/z/tasks
    norm_act = {"i":"init","o":"observe","ctx":"context","sum":"summarize","z":"compress"}.get(act, act)
    valid_actions = {"init", "observe", "context", "summarize", "compress", "tasks"}
    if norm_act not in valid_actions:
        return json.dumps(
            {"error": f"Invalid action '{act}'. Use: {', '.join(sorted(valid_actions))} or short i|o|ctx|sum|z."}
        )
    act = norm_act

    store = _get_store()

    # ── init ──────────────────────────────────────────────────────
    if act == "init":
        if not topic or not topic.strip():
            return json.dumps({"error": "'topic' required for action=init"})

        logger.info(f"session(init): topic='{topic[:60]}', tids={tids}, resume={resume}, tk={tk}")

        session_obj: dict[str, Any] | None = None
        is_resumed = False

        if resume:
            session_obj = store.get_active_session()
            if session_obj is not None:
                sid = session_obj["id"]
                logger.info(f"Resuming active session {sid}")
                existing_ids: list[str] = session_obj.get("task_ids", [])
                new_ids = tids or []
                merged_ids = list(dict.fromkeys(existing_ids + new_ids))
                store.update_session(
                    sid,
                    topic=topic.strip(),
                    task_ids=json.dumps(merged_ids),
                )
                session_obj["topic"] = topic.strip()
                session_obj["task_ids"] = merged_ids
                is_resumed = True

        if session_obj is None:
            result = store.create_session(topic=topic.strip(), task_ids=tids, token_budget=tk)
            session_obj = result
            sid = session_obj["id"]
            is_resumed = False

        sid = session_obj["id"]
        metrics = store.get_session_metrics(sid)
        observations = store.get_latest_observations(sid, limit=5)

        heal_info = {"healed": 0, "violations_found": 0, "violations": []}
        consolidation_info = {
            "has_pending": False,
            "pending_count": 0,
            "oldest_age_days": None,
            "dry_run_summary": "",
        }
        try:
            heal_info = _heal_check()
        except Exception as exc:
            logger.warning(f"Pipeline heal check failed (non-fatal): {exc}")
        try:
            consolidation_info = _consolidation_check()
        except Exception as exc:
            logger.warning(f"Pipeline consolidation check failed (non-fatal): {exc}")

        layer1 = _build_layer1(
            session_obj, metrics, heal_info=heal_info, consolidation_info=consolidation_info
        )
        layer2 = _build_layer2(observations)
        layer3 = _build_layer3(observations)

        return _j(
            {
                "session_id": sid,
                "context": {"layer1": layer1, "layer2": layer2, "layer3": layer3},
                "observations_count": metrics["observations_count"],
                "is_resumed": is_resumed,
                "token_budget": session_obj.get("token_budget", tk),
            }
        )

    # ── observe ───────────────────────────────────────────────────
    if act in ("o", "observe"):
        if not sid:
            return json.dumps({"error": "'session_id' required for action=observe"})
        if not content or not content.strip():
            return json.dumps({"error": "'content' required for action=observe"})
        type = _norm_observe_type(type)
        valid_types = {"dec", "del", "res", "note", "hf", "um", "sys", "decision", "delegation", "result", "handoff", "user_message", "system"}
        if not type or type not in valid_types:
            return json.dumps(
                {"error": f"Invalid type '{type}'. Use short forms: dec|del|res|note|hf|um|sys (or long for compat)."}
            )

        logger.info(f"session(observe): session={sid}, type={type}, agent={agent}")

        session_row = store.get_session(sid)
        if session_row is None:
            logger.warning(f"Session '{sid}' not found — auto-creating")
            session_row = store.ensure_session(sid)

        obs_id = store.add_observation(
            session_id=sid,
            type=type,
            content=content.strip(),
            agent=agent,
            entities=entities,
            handoff_path=hpath,
            task_ref=tref,
            tokens_discovery=tkdc,
            tokens_read=tkrd,
            parent_id=pid,
        )

        entities_found = 0
        if entities:
            for entity_name in entities:
                en = entity_name.strip()
                if not en:
                    continue
                etype = _infer_entity_type(en)
                entity_id = store.get_or_create_entity(en, entity_type=etype)
                store.link_entity_to_observation(obs_id, entity_id)
                entities_found += 1

        if tref:
            task_dict = store.get_task(tref)
            if task_dict is not None:
                try:
                    store.add_task_event(
                        task_id=tref,
                        event_type="note",
                        details=f"[session:{sid[:16]}] {type}: {content[:150]}",
                        handoff_path=hpath,
                    )
                except Exception as exc:
                    logger.debug(f"Direct task event log failed: {exc}")

        session_updated = False
        if type in ("result", "system"):
            store.update_session(sid, topic=session_row.get("topic", ""))
            session_updated = True

        return _j(
            {
                "observation_id": obs_id,
                "entities_found": entities_found,
                "session_updated": session_updated,
            }
        )

    # ── context ───────────────────────────────────────────────────
    if act == "context":
        if not sid:
            return json.dumps({"error": "'session_id' required for action=context"})
        if l not in (1, 2, 3):
            return json.dumps({"error": "layer must be 1, 2, or 3."})

        logger.info(f"session(context): session={sid}, l={l}")

        session_row = store.get_session(sid)
        if session_row is None:
            return json.dumps({"error": f"Session '{sid}' not found"})

        metrics = store.get_session_metrics(sid)
        observations = store.get_latest_observations(sid, limit=5)

        if l == 1:
            ctx = _build_layer1(session_row, metrics)
        elif l == 2:
            ctx = _build_layer2(observations)
        else:
            ctx = _build_layer3(observations)

        if mtk is not None:
            tkc = _count_tokens(ctx)
            if tkc > mtk:
                ctx = _truncate_to_tokens(ctx, mtk)

        return _j(
            {
                "layer": l,
                "context": ctx,
                "token_count": _count_tokens(ctx),
                "has_more": l < 3,
                "suggestion": _LAYER_SUGGESTIONS_CROSS_SESSION.get(l, ""),
            }
        )

    # ── summarize ─────────────────────────────────────────────────
    if act == "summarize":
        if not sid:
            return json.dumps({"error": "'session_id' required for action=summarize"})
        if lv not in (1, 2, 3):
            return json.dumps({"error": "level must be 1, 2, or 3."})

        logger.info(f"session(summarize): session={sid}, force={force}, lv={lv}")

        session_row = store.get_session(sid)
        if session_row is None:
            return json.dumps({"error": f"Session '{sid}' not found"})

        if force:
            candidates = store.get_observations(sid, limit=1000, offset=0)
        else:
            candidates = store.get_summarization_candidates(sid, level=lv)

        if not candidates:
            return _j(
                {
                    "summary_id": None,
                    "level": lv,
                    "observations_compressed": 0,
                    "token_savings": 0,
                    "content": "",
                    "warning": "No new observations to compress.",
                }
            )

        obs_count = len(candidates)
        total_disc = sum(o.get("tokens_discovery", 0) for o in candidates)
        lines: list[str] = []
        for o in candidates:
            prefix = {
                "decision": "🧠 Decision",
                "delegation": "📤 Delegation",
                "result": "📥 Result",
                "note": "📝 Note",
                "handoff": "📄 Handoff",
                "user_message": "💬 User",
                "system": "⚙️ System",
            }.get(o.get("type", ""), "📌")
            lines.append(f"[{prefix} / {o.get('agent', '?')}] {o.get('content', '')[:300]}")

        summary_content = "\n\n".join(lines)
        compressed_tokens = _count_tokens(summary_content)
        token_savings_actual = max(0, total_disc - compressed_tokens)

        summary_id = store.add_summary(
            session_id=sid,
            level=lv,
            content=summary_content,
            token_count=compressed_tokens,
            parent_id=None,
        )

        if lv == 1:
            store.update_session(
                sid,
                summary=summary_content[:500],
                token_discovery=session_row.get("token_discovery", 0),
                token_read=session_row.get("token_read", 0),
            )

        return _j(
            {
                "summary_id": summary_id,
                "level": lv,
                "observations_compressed": obs_count,
                "token_savings": token_savings_actual,
                "content": summary_content,
            }
        )

    # ── compress ──────────────────────────────────────────────────
    if act == "compress":
        if ml not in (1, 2):
            return json.dumps({"error": "max_level must be 1 (warm) or 2 (cold)."})

        logger.info(f"session(compress): days={days}, ml={ml}, dry_run={dry}")

        try:
            obs_results = store.compress_observations(age_days=days, max_level=ml, dry_run=dry)
            task_results = store.compress_task_events(age_days=days, max_level=ml, dry_run=dry)
            combined: dict[str, Any] = {
                "observations_warm": obs_results.get("observations_warm", 0),
                "observations_cold": obs_results.get("observations_cold", 0),
                "summaries_created": obs_results.get("summaries_created", 0),
                "events_warm": task_results.get("events_warm", 0),
                "events_cold": task_results.get("events_cold", 0),
                "dry_run": dry,
                "details": (obs_results.get("details", []) + task_results.get("details", [])),
            }
            return _j(combined)
        except Exception as e:
            logger.error(f"Session compression failed: {e}")
            return json.dumps({"error": f"Session compression failed: {e}"})

    # ── tasks (session_tasks) ─────────────────────────────────────
    if act == "tasks":
        if not sid:
            return json.dumps({"error": "'session_id' required for action=tasks"})
        logger.info(f"session(tasks): session={sid}")
        tasks = store.get_session_tasks(sid)
        return json.dumps(tasks)

    # Should not reach here
    return json.dumps({"error": f"Action '{act}' not implemented."})


# -------------------------------------------------------------------
# Tool 3: task  — task lifecycle (replaces 7 tools)
#
# action: create | query | update | log | summary | export | compress
# -------------------------------------------------------------------


@mcp.tool()
def task(
    act: str = "query",
    # Create-specific
    desc: str | None = None,
    prio: str = "medium",
    owner: str = "Poros",
    status: str = "pending",          # used for create + query + (compat for update)
    tid: str | None = None,
    parent: str | None = None,
    tags: list[str] | None = None,
    # Query-specific
    search: str | None = None,
    tag: str | None = None,
    since: str | None = None,
    limit: int = 20,
    evts: bool = False,
    # Update-specific (sts is the short, token-optimized name)
    sts: str | None = None,           # preferred for act=update (token efficiency)
    note: str | None = None,
    # Log-specific
    evt: str | None = None,
    details: str | None = None,
    hpath: str | None = None,
    # Export-specific
    fmt: bool = True,
    # Compress-specific
    days: int | None = None,
    ml: int = 2,
    dry: bool = True,
    # For escalation context (T-GH-001 P0 #2)
    sid: str | None = None,
) -> str:
    """Task lifecycle: create | query | update | log | summary | export | compress.

    For act="update": prefer the short parameter "sts" (introduced for token reduction
    on frequent status changes/handoffs). "status" is accepted as fallback for compatibility.
    """
    # Normalize act first for token discipline (supports short forms like "u","l")
    norm_act = _norm_task_act(act) or act
    valid_actions = {"c", "q", "u", "l", "sum", "exp", "z", "create", "query", "update", "log", "summary", "export", "compress"}
    if norm_act not in valid_actions and act not in valid_actions:
        return json.dumps(
            {"error": f"Invalid action '{act}'. Use: {', '.join(sorted(valid_actions))}."}
        )

    store = _get_store()

    # Normalize hot input fields for token discipline (accept long or short)
    act = norm_act if norm_act in {"c","q","u","l","sum","exp","z"} else act   # prefer short
    status = _norm_task_status(status)
    sts = _norm_task_status(sts)
    evt = _norm_event_type(evt)

    # ── create ────────────────────────────────────────────────────
    if act in ("c", "create"):
        from tools.synapsis.models import (
            INITIAL_STATUSES,
            TASK_ID_REGEX,
            extract_area_from_description,
            extract_area_from_task_id,
            truncate_description,
            validate_priority,
            validate_status,
        )

        act = _norm_task_act(act) or act  # normalize early
        status = _norm_task_status(status)

        if not desc or not desc.strip():
            return json.dumps({"error": "'description' required for action=create"})

        desc, was_truncated = truncate_description(desc.strip(), max_len=150)
        if was_truncated:
            logger.warning(f"Description truncated to 150 chars: {desc[:80]}...")

        try:
            validate_priority(prio)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        try:
            validate_status(status)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        if status not in INITIAL_STATUSES:
            return json.dumps(
                {
                    "error": f"Status '{status}' invalid for creation. Use: {', '.join(INITIAL_STATUSES)}."
                }
            )

        if tags is not None:
            cleaned_tags: list[str] = []
            for t in tags:
                tg = t.strip()
                if " " in tg:
                    return json.dumps(
                        {
                            "error": f"Tag '{tg}' contiene spazi. I tag devono essere una singola parola."
                        }
                    )
                if tg:
                    cleaned_tags.append(tg)
            tags = cleaned_tags

        final_id: str | None = tid
        if final_id is not None:
            if not TASK_ID_REGEX.match(final_id):
                return json.dumps(
                    {"error": f"Invalid ID '{final_id}'. Format must be T-<AREA>-<NNN>."}
                )
            if store.get_task(final_id) is not None:
                return json.dumps({"error": f"ID '{final_id}' already in use."})
        else:
            if parent:
                pt = store.get_task(parent)
                if pt is None:
                    return json.dumps({"error": f"Parent task '{parent}' not found."})
                area = extract_area_from_task_id(pt["id"])
            else:
                area = extract_area_from_description(desc)
            final_id = store.next_task_id(area)

        if parent is not None:
            pt = store.get_task(parent)
            if pt is None:
                return json.dumps({"error": f"Parent task '{parent}' not found."})

        result = store.create_task(
            task_id=final_id,
            description=desc,
            status=status,
            priority=prio,
            owner=owner,
            tags=tags or [],
            parent=parent,
        )
        logger.info(f"Task created: {final_id} (status={status}, owner={owner})")
        return json.dumps(
            {
                "id": final_id,
                "status": status,
                "created_at": result.get("created_at", ""),
                "description": desc,
            }
        )

    # ── query ─────────────────────────────────────────────────────
    if act in ("q", "query"):
        logger.info(
            f"task(query): status={status}, owner={owner}, prio={prio}, tid={tid}, search={search}, tag={tag}"
        )

        if limit < 1:
            limit = 20
        if limit > 100:
            limit = 100

        if since:
            try:
                from datetime import datetime as dt_cls

                dt_cls.fromisoformat(since)
            except ValueError:
                return json.dumps({"error": f"Invalid 'since' format: '{since}'. Use ISO 8601."})

        if tid:
            t = store.get_task(tid)
            if t is None:
                return "[]"
            return json.dumps([_task_dict_to_json(t, include_events=evts)])

        results = store.get_tasks(
            status=status,
            owner=owner,
            priority=prio,
            parent=parent,
            tag=tag,
            search=search,
            since=since,
            limit=limit,
        )
        output = [_task_dict_to_json(t, include_events=evts) for t in results]
        return json.dumps(output)

    # ── update ────────────────────────────────────────────────────
    # Note: "sts" (short for status) is the intentional token-efficient parameter
    # for status updates. It was introduced to reduce tokens on a very frequent
    # operation (task state changes, handoff logging, etc.).
    # For backward compatibility with older docs/examples, we also accept "status"
    # when "sts" is not provided for act=update.
    if act in ("u", "update"):
        from tools.synapsis.models import StateMachine, validate_status

        if not tid:
            return json.dumps({"error": "'tid' required for action=update"})

        # Prefer the short 'sts' (token optimization, the deliberate design choice).
        # Fall back to 'status' only if it differs from the (normalized) create default.
        effective_status = sts
        if effective_status is None:
            if status not in (None, "pend", "pending"):
                effective_status = status
            else:
                effective_status = None

        if not effective_status:
            return json.dumps({"error": "'sts' required for action=update (short token-efficient name for new status); 'status' is accepted for compatibility when using a non-default value"})

        try:
            validate_status(effective_status)
        except ValueError as e:
            return json.dumps({"error": str(e)})

        task_dict = store.get_task(tid)
        if task_dict is None:
            return json.dumps({"error": f"Task '{tid}' not found"})

        old_status = _norm_task_status(task_dict.get("status", "pend"))
        if old_status == effective_status:
            return _j(
                {
                    "id": tid,
                    "old_status": old_status,
                    "new_status": effective_status,
                    "updated_at": task_dict.get("updated_at"),
                    "auto_parent_completed": None,
                    "warning": f"Task already in state '{effective_status}'.",
                }
            )

        try:
            StateMachine.validate_transition(old_status, effective_status)
        except ValueError as e:
            return json.dumps({"error": str(e)})

        result = store.update_task_status(task_id=tid, new_status=effective_status, note=note)

        # T-GH-001: auto-escalation on blk (improved for P0 #2 + P2 #6 workpad)
        if effective_status == "blk":
            try:
                from tools.synapsis.report import report_problem

                report_problem(
                    title=f"Task {tid} blocked",
                    body=note or "(no note provided on blk transition)",
                    tref=tid,
                    sid=sid,
                    error=note or "(no note provided on blk transition)",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Auto-escalation on blk failed (non-fatal): {exc}")

        return _j(result)

    # ── log ───────────────────────────────────────────────────────
    if act in ("l", "log"):
        from tools.synapsis.models import now_iso_seconds, validate_event_type

        if not tid:
            return json.dumps({"error": "'task_id' required"})
        if not details or not details.strip():
            return json.dumps({"error": "'details' required"})
        try:
            validate_event_type(evt)
        except ValueError as e:
            return json.dumps({"error": str(e)})

        task_dict = store.get_task(tid)
        if task_dict is None:
            return json.dumps({"error": f"Task '{tid}' not found"})

        if evt == "handoff_ref" and not hpath:
            logger.warning(f"handoff_ref event on {tid} without hpath")

        store.add_task_event(
            task_id=tid,
            event_type=evt,
            details=details.strip(),
            handoff_path=hpath,
        )
        events = store.get_task_events(tid, limit=1000)
        event_index = len(events) - 1
        ts = now_iso_seconds()

        return json.dumps(
            {
                "id": tid,
                "event_index": event_index,
                "timestamp": ts,
                "event_type": evt,
                "details": details.strip(),
            }
        )

    # ── summary ───────────────────────────────────────────────────
    if act in ("sum", "summary"):
        from tools.synapsis.models import VALID_PRIORITIES, VALID_STATUSES

        logger.info(f"task(summary): owner={owner}")

        all_tasks = (
            store.get_tasks(owner=owner, limit=10000) if owner else store.get_tasks(limit=10000)
        )
        total = len(all_tasks)
        by_status: dict[str, int] = {s: 0 for s in VALID_STATUSES}
        by_priority: dict[str, int] = {p: 0 for p in VALID_PRIORITIES}
        wip_current: list[str] = []
        oldest_pending: str | None = None
        oldest_pending_ts: str | None = None

        for t in all_tasks:
            s = _norm_task_status(t.get("status", "pend"))
            by_status[s] = by_status.get(s, 0) + 1
            p = t.get("priority", "medium")
            by_priority[p] = by_priority.get(p, 0) + 1
            if s == "prog":
                wip_current.append(t["id"])
            if s == "pend":
                created = t.get("created_at", "")
                if oldest_pending_ts is None or created < oldest_pending_ts:
                    oldest_pending_ts = created
                    oldest_pending = f"{t['id']} ({created})"

        return _j(
            {
                "total": total,
                "by_status": by_status,
                "by_priority": by_priority,
                "wip_current": wip_current,
                "oldest_pending": oldest_pending or None,
            }
        )

    # ── export ────────────────────────────────────────────────────
    if act in ("exp", "export"):
        logger.info("task(export) called")
        tasks_list = store.get_tasks(limit=10000)
        tasks_dict: dict[str, Any] = {}
        counters: dict[str, int] = {}

        for t in tasks_list:
            tid = t["id"]
            events = store.get_task_events(tid, limit=1000)
            legacy_events: list[dict[str, Any]] = []
            for ev in events:
                legacy_event: dict[str, Any] = {
                    "timestamp": ev["created_at"],
                    "type": ev["type"],
                    "details": ev["details"],
                }
                if ev.get("handoff_path"):
                    legacy_event["handoff_path"] = ev["handoff_path"]
                legacy_events.append(legacy_event)

            tasks_dict[tid] = {
                "id": tid,
                "description": t.get("description", ""),
                "status": _norm_task_status(t.get("status", "pend")),
                "priority": t.get("priority", "medium"),
                "owner": t.get("owner", "Poros"),
                "tags": t.get("tags", []),
                "parent": t.get("parent"),
                "handoff_refs": t.get("handoff_refs", []),
                "events": legacy_events,
                "created_at": t.get("created_at", ""),
                "updated_at": t.get("updated_at", ""),
            }

        area_counts: dict[str, int] = {}
        for tid in tasks_dict:
            parts = tid.split("-")
            if len(parts) >= 3 and parts[0] == "T":
                area = "-".join(parts[1:-1])
                num_str = parts[-1]
                try:
                    num = int(num_str)
                    area_counts[area] = max(area_counts.get(area, 0), num)
                except ValueError:
                    pass

        import yaml as yaml_lib

        data: dict[str, Any] = {
            "version": 1,
            "last_updated": max((t.get("updated_at", "") for t in tasks_list), default=""),
            "counter": counters,
            "tasks": tasks_dict,
        }
        if fmt:
            yaml_str = yaml_lib.dump(
                data, default_flow_style=False, allow_unicode=True, sort_keys=False, indent=2
            )
        else:
            yaml_str = yaml_lib.dump(data, allow_unicode=True, sort_keys=False)
        return yaml_str

    # ── compress ──────────────────────────────────────────────────
    if act in ("z", "compress"):
        if ml not in (1, 2):
            return json.dumps({"error": "max_level must be 1 (warm) or 2 (cold)."})
        logger.info(f"task(compress): days={days}, ml={ml}, dry_run={dry}")
        try:
            results = store.compress_task_events(age_days=days, max_level=ml, dry_run=dry)
            return json.dumps(results)
        except Exception as e:
            logger.error(f"Compression failed: {e}")
            return json.dumps({"error": f"Compression failed: {e}"})

    return json.dumps({"error": f"Action '{act}' not implemented."})


# -------------------------------------------------------------------
# Helper: _task_dict_to_json (shared by task query)
# -------------------------------------------------------------------


def _task_dict_to_json(task_dict: dict[str, Any], include_events: bool = False) -> dict[str, Any]:
    """Convert a task dict from SynapsisStore to a JSON-friendly output dict."""
    store = _get_store()
    events = store.get_task_events(task_dict["id"], limit=1000) if include_events else []
    legacy_events: list[dict[str, Any]] = []
    for ev in events:
        legacy_event: dict[str, Any] = {
            "timestamp": ev["created_at"],
            "type": ev["type"],
            "details": ev["details"],
        }
        if ev.get("handoff_path"):
            legacy_event["handoff_path"] = ev["handoff_path"]
        legacy_events.append(legacy_event)

    return {
        "id": task_dict["id"],
        "description": task_dict.get("description", ""),
        "status": task_dict.get("status", "pend"),
        "priority": task_dict.get("priority", "medium"),
        "owner": task_dict.get("owner", "Poros"),
        "created_at": task_dict.get("created_at", ""),
        "updated_at": task_dict.get("updated_at", ""),
        "tags": task_dict.get("tags", []),
        "parent": task_dict.get("parent"),
        "handoff_refs": task_dict.get("handoff_refs", []),
        "compression_level": task_dict.get("compression_level", 0),
        "event_count": len(events),
    }


# -------------------------------------------------------------------
# Tool 4: admin  — system administration (replaces 7 tools)
#
# action: health | domain | orphan | vacuum | stats | index | checkpoint
# -------------------------------------------------------------------


@mcp.tool()
def admin(
    act: str = "stats",
    # Health-specific
    cmd: str = "quick",
    # Domain-specific
    domain: str | None = None,
    on: bool = True,
    # Index-specific
    ix: str = "status",
    dry: bool = False,
    # Checkpoint-specific
    name: str = "auto",
    # Stats-specific
    scope: str = "all",
) -> str:
    """System admin: health | domain | orphan | vacuum | stats | index | checkpoint."""
    valid_actions = {"health", "domain", "orphan", "vacuum", "stats", "index", "checkpoint"}
    if act not in valid_actions:
        return json.dumps(
            {"error": f"Invalid action '{act}'. Use: {', '.join(sorted(valid_actions))}."}
        )

    store = _get_store()

    # ── health ────────────────────────────────────────────────────
    if act == "health":
        logger.info(f"admin(health): cmd={cmd}")
        result = store.db_health_check(cmd=cmd)
        return json.dumps(result)

    # ── domain ────────────────────────────────────────────────────
    if act == "domain":
        if not domain:
            return json.dumps({"error": "'domain' required for action=domain"})
        logger.info(f"admin(domain): domain={domain}, on={on}")
        protected_domains = {"admin", "system"}
        if domain.lower() in protected_domains:
            return json.dumps(
                {
                    "error": "domain_protected",
                    "domain": domain,
                    "message": f"Domain '{domain}' is protected and cannot be deactivated.",
                }
            )
        if not store.set_domain_active(domain.lower(), on):
            return json.dumps(
                {
                    "error": "domain_not_found",
                    "domain": domain,
                    "message": f"Domain '{domain}' not found.",
                }
            )
        return json.dumps({"success": True, "domain": domain.lower(), "is_active": on})

    # ── orphan ────────────────────────────────────────────────────
    if act == "orphan":
        logger.info("admin(orphan) called")
        results = store.orphan_scan()
        return json.dumps(results, indent=2)

    # ── vacuum ────────────────────────────────────────────────────
    if act == "vacuum":
        logger.info("admin(vacuum) called")
        result = store.vacuum()
        return json.dumps(result)

    # ── stats ─────────────────────────────────────────────────────
    if act == "stats":
        logger.info(f"admin(stats): scope={scope}")
        if scope == "basic":
            return json.dumps(store.get_stats())
        elif scope == "health":
            health_score = store.compute_health_score()
            orphans = store.orphan_scan()
            return _j({"health_score": health_score, "orphan_tasks": orphans})
        else:
            return json.dumps(store.get_stats(enhanced=True))

    # ── index ─────────────────────────────────────────────────────
    if act == "index":
        valid_index_actions = {"status", "update", "rebuild", "clean"}
        if ix not in valid_index_actions:
            return json.dumps(
                {
                    "error": f"Invalid index_action '{ix}'. Use: {', '.join(sorted(valid_index_actions))}."
                }
            )

        logger.info(f"admin(index): action={ix}, dry_run={dry}")

        if ix == "status":
            stats_info: dict[str, Any] = {"chunks": 0, "files": 0, "last_upd": None, "size_kb": 0}
            try:
                total_chunks = store._conn.execute("SELECT COUNT(*) AS cnt FROM chunks").fetchone()[
                    "cnt"
                ]
                total_files = store._conn.execute(
                    "SELECT COUNT(DISTINCT file_path) AS cnt FROM chunks"
                ).fetchone()["cnt"]
                last_upd = store._conn.execute(
                    "SELECT MAX(last_indexed_at) AS last_update FROM file_state"
                ).fetchone()
                last_update = last_upd["last_update"] if last_upd else None
                db_size_kb = store.path.stat().st_size // 1024
                stats_info = {
                    "chunks": total_chunks,
                    "files": total_files,
                    "last_upd": last_update,
                    "size_kb": db_size_kb,
                }
            except Exception:
                pass  # Fallback to zeroed stats
            return _j({"action": "status", **stats_info})

        domain_data = store.get_domain("knowledge")
        if domain_data is not None and not domain_data.get("is_active", 1):
            return json.dumps(
                {"error": "Knowledge domain is disabled. Enable it first via admin(domain)."}
            )

        try:
            from tools.knowledge_base import chunk_indexer
        except ImportError as exc:
            return json.dumps({"error": f"chunk_indexer not available: {exc}."})

        try:
            if ix == "update":
                chunk_indexer.update(verbose=False, dry_run=dry)
                return json.dumps({"action": "update", "dry_run": dry, "status": "completed"})
            if ix == "rebuild":
                chunk_indexer.rebuild(verbose=False)
                return json.dumps({"action": "rebuild", "status": "completed"})
            if ix == "clean":
                chunk_indexer.clean(verbose=False, dry_run=dry)
                return json.dumps({"action": "clean", "dry_run": dry, "status": "completed"})
        except Exception as exc:
            # Surface configuration errors (e.g. missing knowledge.include) and other
            # problems as clear structured errors instead of letting the call explode.
            return json.dumps({"error": str(exc), "action": ix})

        return json.dumps({"error": f"Unhandled index_action '{ix}'."})

    # ── checkpoint ────────────────────────────────────────────────
    if act == "checkpoint":
        # checkpoint uses the name param; we interpret it differently
        # based on the MCP tool name, but here it's just create+restore
        logger.info(f"admin(checkpoint): name={name}")
        result = store.checkpoint_create(name=name)
        return json.dumps(result)

    return json.dumps({"error": f"Action '{act}' not implemented."})


# -------------------------------------------------------------------
# Tool 5: consolidate  — sleep-cycle consolidation
# (unchanged from original, just cleaned)
# -------------------------------------------------------------------


@mcp.tool()
def consolidate(
    sid: str | None = None,
    days: int = 7,
    dry: bool = True,
    auto: bool = True,
) -> str:
    """Distil unconsolidated observations into structured memory layers.

    When ``auto=True`` (default), runs a lightweight auto-consolidation check
    that only triggers actual compression when clearly needed (>20 unconsolidated
    obs, obs older than 7 days, or >50 total obs in session). When ``auto=False``,
    runs the full explicit consolidation as before.
    """
    logger.info(f"consolidate: session={sid}, days={days}, dry_run={dry}, auto={auto}")

    store = _get_store()

    # Auto mode: lightweight check, only consolidates if clearly needed
    if auto:
        result = store.auto_consolidate_if_needed(session_id=sid)

        # T-GH-001 P0 #2 + P2 #6: escalate on hygiene pain (auto)
        if result.get("triggered"):
            try:
                from tools.synapsis.report import report_problem

                report_problem(
                    title=f"Consolidate hygiene pain detected (sid={sid or 'N/A'})",
                    body=(
                        f"Auto-consolidation triggered: {result.get('reason')}\n"
                        f"Consolidated: {result.get('consolidated', 0)} obs\n"
                        f"Candidates: {result.get('candidate_count', '?')}"
                    ),
                    sid=sid,
                    error=f"Auto-consolidation triggered: {result.get('reason')}",
                    analysis="Review consolidated observations and contradictions; consider follow-up task or fix.",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Auto-escalation on consolidate hygiene failed (non-fatal): {exc}")

        return json.dumps(result)

    # Explicit mode: original full consolidation logic
    candidates = store.get_non_consolidated_observations(
        session_id=sid,
        age_days=days,
        max_results=200,
    )

    if not candidates:
        return _j(
            {
                "consolidated": 0,
                "patterns_detected": 0,
                "contradictions_detected": 0,
                "memory_layers_created": 0,
                "dry_run": dry,
                "message": "No observations to consolidate.",
            }
        )

    session_groups: dict[str, list[dict[str, Any]]] = {}
    for obs in candidates:
        sid = obs.get("session_id", "?")
        session_groups.setdefault(sid, []).append(obs)

    contradictions = store.detect_contradictions(candidates)

    entity_counts: dict[str, int] = {}
    for obs in candidates:
        for ent in obs.get("entities", []):
            ent_lower = ent.strip().lower()
            entity_counts[ent_lower] = entity_counts.get(ent_lower, 0) + 1

    top_entities = sorted(entity_counts.items(), key=lambda x: -x[1])[:10]
    patterns_detected = len(top_entities)
    contradictions_detected = len(contradictions)

    # T-GH-001 P0 #2 + P2 #6: escalate on hygiene pain (contradictions)
    if contradictions_detected > 0:
        try:
            from tools.synapsis.report import report_problem

            report_problem(
                title=f"Consolidate detected contradictions (sid={sid or 'N/A'})",
                body=(
                    f"Contradictions detected: {contradictions_detected}\n"
                    f"Patterns detected: {patterns_detected}\n"
                    f"Candidates consolidated: {len(candidates)}"
                ),
                sid=sid,
                error=f"Contradictions detected: {contradictions_detected}",
                analysis="Investigate contradictions in observations; create follow-up task or handoff if needed.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Auto-escalation on consolidate contradictions failed (non-fatal): {exc}")

    if not dry:
        memory_layers_created = 0
        for sid, obs_list in session_groups.items():
            context_content = "\n".join(
                f"[{o.get('type', '?')}/{o.get('agent', '?')}] {o.get('content', '')[:200]}"
                for o in obs_list[:20]
            )
            try:
                store.set_memory_layer(
                    session_id=sid, layer="context", content=context_content[:1000]
                )
                memory_layers_created += 1
            except Exception as exc:
                logger.debug(f"Failed to set memory layer for {sid}: {exc}")

            for contradiction in contradictions:
                related_obs = [
                    o
                    for o in obs_list
                    if o["id"] in contradiction.get("positive_observations", [])
                    or o["id"] in contradiction.get("negative_observations", [])
                ]
                if related_obs:
                    warning_content = f"Contradiction detected: {contradiction['description']}\nEntities: {', '.join(contradiction['entities'])}"
                    try:
                        store.set_memory_layer(
                            session_id=sid, layer="caveat", content=warning_content[:500]
                        )
                        memory_layers_created += 1
                    except Exception as exc:
                        logger.debug(f"Failed to set caveat layer for {sid}: {exc}")

        result = {
            "consolidated": len(candidates),
            "patterns_detected": patterns_detected,
            "contradictions_detected": contradictions_detected,
            "memory_layers_created": memory_layers_created,
            "sessions_affected": list(session_groups.keys()),
            "dry_run": dry,
        }
    else:
        result = {
            "consolidated": len(candidates),
            "patterns_detected": patterns_detected,
            "contradictions_detected": contradictions_detected,
            "memory_layers_created": 0,
            "sessions_affected": list(session_groups.keys()),
            "dry_run": dry,
            "top_entities": [{"name": n, "count": c} for n, c in top_entities],
            "contradictions": contradictions,
        }

    return _j(result)


# ===================================================================
#  Tool 6: hf
# ===================================================================


@mcp.tool()
def hf(
    act: str = "new",
    # new params
    type: str | None = None,
    title: str | None = None,
    body: str | None = None,
    agent: str | None = None,
    tref: str | None = None,
    note: str | None = None,
    refs: str | None = None,
    devi: str | None = None,
    st: str = "done",
    prio: str = "med",
    # Backward compat — deprecated alias for tref
    task: str | None = None,
    # get params
    ref: str | None = None,
    tk: int = 300,
    q: str | None = None,
) -> str:
    """Handoff: act=new (create) | act=get (read). Compact params: act, st, prio, tk, q."""
    # Deprecated: map 'task' to 'tref' (from old MCP schema)
    if tref is None and task is not None:
        tref = task
    store = _get_store()
    from tools.common.paths import project_root

    proj_root = project_root()

    if act in ("n", "new"):
        # Validate required params
        if not type or not title or not body or not agent:
            return json.dumps(
                {"error": "'type', 'title', 'body', e 'agent' sono obbligatori per act=new."}
            )

        if len(title) > 60:
            logger.warning(f"Title exceeds 60 characters ({len(title)} chars).")

        from tools.synapsis.hf import hf_new

        result = hf_new(
            store=store,
            project_root=proj_root,
            type=type,
            title=title,
            body=body,
            agent=agent,
            tref=tref,
            note=note,
            refs=refs,
            devi=devi,
            st=st,
            prio=prio,
        )
        return json.dumps(result, ensure_ascii=False)

    elif act in ("g", "get"):
        if not ref:
            return json.dumps({"error": "'ref' required for act=get"})

        from tools.synapsis.hf import hf_get

        result = hf_get(
            store=store,
            project_root=proj_root,
            ref=ref,
            tk=tk,
            q=q,
        )
        return json.dumps(result, ensure_ascii=False)

    else:
        return json.dumps({"error": f"Invalid action '{act}'. Use 'new' or 'get'."})


# ===================================================================
#  Tools 7 & 8: d_set, d_get — deliverable file registry
# ===================================================================


@mcp.tool()
def d_set(p: str) -> str:
    """Register file path. Returns hash."""
    if not p or not p.strip():
        return json.dumps({"error": "'p' required"})
    store = _get_store()
    h = store.deliverable_register(p.strip())
    return json.dumps({"h": h})


@mcp.tool()
def d_get(h: str, l: int = 1) -> str:  # noqa: E741
    """Resolve hash. l=1: meta. l=2: +500ch. l=3: full."""
    if not h or not h.strip():
        return json.dumps({"error": "'h' required"})
    store = _get_store()
    result = store.deliverable_read(h.strip(), layer=l)
    if result is None:
        return json.dumps({"error": "not_found"})
    return json.dumps(result)


# ===================================================================
#  CONTEXT BUILDERS  (3-layer progressive disclosure)
# ===================================================================


def _build_layer1(
    session: dict[str, Any],
    metrics: dict[str, Any],
    heal_info: dict[str, Any] | None = None,
    consolidation_info: dict[str, Any] | None = None,
) -> str:
    """Build Layer 1 — header with topic, duration, metrics, tasks, entities (~200 tokens)."""
    session_id = session.get("id", "?")
    topic = session.get("topic", "?")
    started = session.get("started_at", "?")[:16]  # trim seconds
    obs_count = metrics.get("observations_count", 0)
    tot_disc = metrics.get("total_tokens_discovery", 0)
    tot_read = metrics.get("total_tokens_read", 0)
    savings_pct = metrics.get("token_savings_avg", 0) * 100

    task_ids = session.get("task_ids", [])

    lines = [
        f" Session: {topic}",
        f" Started: {started} | Observations: {obs_count}",
    ]

    if tot_disc > 0:
        lines.append(
            f" Token Economics: discovery={tot_disc} | read={tot_read} | savings={savings_pct:.0f}%"
        )

    if task_ids:
        lines.append("")
        lines.append(" Active Tasks:")
        for tid in task_ids:
            lines.append(f"   {tid}")

    # Get key entities from latest observations
    try:
        store = _get_store()
        latest = store.get_latest_observations(session_id, limit=3)
        all_entities: set[str] = set()
        for obs in latest:
            for ent in obs.get("entities", []):
                all_entities.add(ent)
        if all_entities:
            lines.append("")
            lines.append(f" Key Entities: {', '.join(sorted(all_entities)[:8])}")
    except Exception:
        pass

    # Append heal check info if available
    if heal_info is not None:
        lines.append("")
        vf = heal_info.get("violations_found", 0)
        h = heal_info.get("healed", 0)
        parts = [f" FK violations: {vf}"]
        if h > 0:
            parts.append(f"auto-healed: {h}")
        lines.append(f"   Heal Check: {' | '.join(parts)}")

        # Health score warning (Self-Healing Memory)
        health_score = heal_info.get("health_score")
        if health_score is not None:
            score = health_score.get("score", 100)
            if score < 60:
                lines.append(
                    f"   ⚠ DB Health Score: {score}/100 — consider running db_health_check"
                )
            elif score < 30:
                lines.append(f"   🚨 DB Health Score: {score}/100 — auto-repair recommended")

        # Orphan task warning
        orphans = heal_info.get("orphans", [])
        if orphans:
            lines.append(f"   ⚠ Orphan Tasks: {len(orphans)} found (stale in_progress)")

    # Append consolidation check info if available
    if consolidation_info is not None:
        lines.append("")
        if consolidation_info.get("has_pending"):
            pc = consolidation_info.get("pending_count", 0)
            oa = consolidation_info.get("oldest_age_days", "?")
            lines.append(
                f"   Consolidation Check: {pc} observations pending (oldest {oa} days old)"
            )
        else:
            lines.append("   Consolidation Check: up to date")

    lines.append("")
    lines.append("(Use layer=2 for timeline details)")

    return "\n".join(lines)


def _build_layer2(observations: list[dict[str, Any]]) -> str:
    """Build Layer 2 — timeline of last 5 observations with type, snippet, entities (~800 tokens)."""
    if not observations:
        return " Timeline: (no observations)"

    lines = [" Timeline (latest observations):", ""]

    for obs in observations:
        t = obs.get("created_at", "")[11:16]  # HH:MM
        otype = obs.get("type", "?")
        content = obs.get("content", "")[:120]
        entities_list = obs.get("entities", [])
        savings_pct = obs.get("token_savings", 0) * 100

        task_ref = obs.get("task_ref")
        handoff_path = obs.get("handoff_path")

        lines.append(f"[{t}]  {otype} — {content}")
        if entities_list:
            lines.append(f"       Entities: {', '.join(entities_list[:5])}")
        if task_ref:
            lines.append(f"       Task: {task_ref}")
        if handoff_path:
            lines.append(f"       Handoff: {handoff_path}")
        lines.append(f"       Token savings: {savings_pct:.0f}%")
        lines.append("")

    lines.append('Use "layer=3" for full details | "recall <query>" for search')

    return "\n".join(lines)


def _build_layer3(observations: list[dict[str, Any]]) -> str:
    """Build Layer 3 — most recent observation in full (~1500 tokens)."""
    if not observations:
        return " No observations available."

    latest = observations[0]
    content = latest.get("content", "")
    otype = latest.get("type", "?")
    agent = latest.get("agent", "Poros")
    created = latest.get("created_at", "?")
    entities_list = latest.get("entities", [])
    task_ref = latest.get("task_ref")
    handoff_path = latest.get("handoff_path")
    tokens_disc = latest.get("tokens_discovery", 0)
    tokens_read = latest.get("tokens_read", 0)

    lines = [
        " Ultima osservazione (full):",
        f" Type: {otype} | Agent: {agent} | Time: {created}",
        "",
        content,
        "",
    ]

    if entities_list:
        lines.append(f"Entities: {', '.join(entities_list)}")
    if task_ref:
        lines.append(f"Task: {task_ref}")
    if handoff_path:
        lines.append(f"Handoff: {handoff_path}")

    if tokens_disc > 0 or tokens_read > 0:
        if tokens_disc > 0:
            savings_pct = (tokens_disc - tokens_read) * 100.0 / tokens_disc
        else:
            savings_pct = 0.0
        line = (
            f"Token economics: discovery={tokens_disc}"
            f" | read={tokens_read} | savings={savings_pct:.0f}%"
        )
        lines.append(line)

    return "\n".join(lines)


# ===================================================================
#  PIPELINE HELPERS  (session_init pipeline steps)
# ===================================================================


def _heal_check() -> dict[str, Any]:
    """Run FK check, auto-repair dangling refs, health check & orphan scan (<50ms).

    Returns:
        Dict with healed, violations_found, violations, health, orphans, health_score.
    """
    store = _get_store()
    violations: list[dict[str, Any]] = []

    # --- FK check (original) ---
    try:
        cursor = store._conn.execute("PRAGMA foreign_key_check")
        rows = cursor.fetchall()
    except Exception as exc:
        logger.warning(f"Foreign key check failed: {exc}")
        rows = []

    violations_found = len(rows)
    healed = 0

    for row in rows:
        # sqlite3.Row has named columns: table, rowid, parent, fkid
        table: str = row["table"] if hasattr(row, "keys") else row[0]
        rowid: int = row["rowid"] if hasattr(row, "keys") else row[1]
        parent: str = row["parent"] if hasattr(row, "keys") else row[2]
        fkid: int = row["fkid"] if hasattr(row, "keys") else row[3]

        violations.append(
            {
                "table": table,
                "rowid": rowid,
                "parent": parent,
                "fkid": fkid,
            }
        )

        if table == "observations" and parent == "sessions":
            try:
                obs_row = store._conn.execute(
                    "SELECT session_id FROM observations WHERE rowid = ?",
                    (rowid,),
                ).fetchone()
                if obs_row:
                    session_id: str = (
                        obs_row["session_id"] if hasattr(obs_row, "keys") else obs_row[0]
                    )
                    store.ensure_session(
                        session_id,
                        topic="auto-recovered (heal check)",
                    )
                    healed += 1
                    logger.info(
                        f"Healed orphan observation rowid={rowid} → created session {session_id}"
                    )
            except Exception as exc:
                logger.warning(f"Failed to heal observation rowid={rowid}: {exc}")

        if table != "observations" or parent != "sessions":
            logger.debug(
                f"Non-repairable FK violation: table={table}, "
                f"rowid={rowid}, parent={parent}, fkid={fkid}"
            )

    # --- Health check (Self-Healing Memory) ---
    health: dict[str, Any] | None = None
    orphans: list[dict[str, Any]] = []
    health_score: dict[str, Any] | None = None

    try:
        health = store.db_health_check("quick")
    except Exception as exc:
        logger.warning(f"Health check failed (non-fatal): {exc}")

    try:
        orphans = store.orphan_scan()
    except Exception as exc:
        logger.warning(f"Orphan scan failed (non-fatal): {exc}")

    try:
        health_score = store.compute_health_score()
    except Exception as exc:
        logger.warning(f"Health score computation failed (non-fatal): {exc}")

    return {
        "healed": healed,
        "violations_found": violations_found,
        "violations": violations,
        "health": health,
        "orphans": orphans,
        "health_score": health_score,
    }


def _consolidation_check() -> dict[str, Any]:
    """Check for unconsolidated observations and trigger auto-consolidation if needed.

    Delegates to ``store.auto_consolidate_if_needed()`` for both detection
    and automatic triggering. Reports the result as a structured summary.

    Returns:
        Dict with has_pending, pending_count, oldest_age_days, dry_run_summary,
        and auto_consolidation result.
    """
    store = _get_store()

    try:
        # Run the auto-consolidation check (this is a noop if no trigger met,
        # and runs actual compression if criteria are satisfied)
        auto_result = store.auto_consolidate_if_needed(session_id=None)

        # Build summary from the auto-consolidation result
        triggered = auto_result.get("triggered", False)
        consolidated = auto_result.get("consolidated", 0)
        reason = auto_result.get("reason", "unknown")
        candidate_count = auto_result.get("candidate_count", 0)
        oldest_age = auto_result.get("oldest_age_days")
        error = auto_result.get("error")

        has_pending = candidate_count > 0 and not triggered

        if error:
            dry_run_summary = f"Auto-consolidation error: {error}"
        elif triggered:
            dry_run_summary = (
                f"Auto-consolidation triggered ({reason}): {consolidated} observations consolidated"
            )
        elif has_pending:
            parts: list[str] = [f"{candidate_count} observations pending"]
            if oldest_age is not None:
                parts.append(f"oldest {oldest_age}d old")
            dry_run_summary = " | ".join(parts)
        else:
            dry_run_summary = "No observations to consolidate."

        return {
            "has_pending": has_pending,
            "pending_count": candidate_count,
            "oldest_age_days": oldest_age,
            "dry_run_summary": dry_run_summary,
            "auto_consolidation": {
                "triggered": triggered,
                "consolidated": consolidated,
                "reason": reason,
            },
        }

    except Exception as exc:
        logger.warning(f"Consolidation check failed: {exc}")
        return {
            "has_pending": False,
            "pending_count": 0,
            "oldest_age_days": None,
            "dry_run_summary": "",
            "auto_consolidation": {
                "triggered": False,
                "consolidated": 0,
                "reason": f"error: {exc}",
            },
        }


def _count_tokens(text: str) -> int:
    """Rough token count (4 chars per token heuristic)."""
    return max(1, len(text) // 4)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately ``max_tokens``."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[...truncated...]"


def _infer_entity_type(name: str) -> str:
    """Heuristic entity type inference from name."""
    name_lower = name.lower().strip()

    # Known agent names
    agents = {
        "hermes",
        "proteo",
        "atena",
        "clio",
        "dike",
        "efesto",
        "eunomia",
        "euterpe",
        "metis",
        "pythagoras",
        "hermione",
    }
    if name_lower in agents:
        return "agent"

    tech_indicators = {
        "sqlite",
        "python",
        "mcp",
        "api",
        "sdk",
        "cli",
        "db",
        "yaml",
        "json",
        "fts5",
        "fastmcp",
        "typer",
        "pydantic",
        "loguru",
        "pytest",
        "ruff",
        "mypy",
        "httpx",
        "git",
    }
    if name_lower in tech_indicators:
        return "technology"

    if name_lower.startswith("t-") or name_lower.startswith("fase"):
        return "task"
    if "chimer" in name_lower:
        return "project"

    return "concept"


# ===================================================================
#  Entry point
# ===================================================================


def main_server() -> None:
    """Start the Synapsis MCP server on stdio transport."""
    if not MCP_AVAILABLE:
        logger.error("MCP SDK not installed. Run: uv add mcp")
        import sys

        sys.exit(1)

    logger.info("Starting Synapsis MCP server on stdio...")

    # Validate DB is accessible on startup
    try:
        store = _get_store()
        stats = store.get_stats()
        logger.info(
            f"Synapsis DB ready at {store.path}: "
            f"{stats['sessions']['total']} sessions, "
            f"{stats['tasks']['total']} tasks, "
            f"{stats['observations']} observations"
        )
        # NOTE: do NOT close the store here — _get_store() returns the same
        # singleton instance, and closing would set _conn=None, breaking all
        # subsequent tool calls with "'NoneType' object has no attribute 'execute'"
    except Exception as e:
        logger.error(f"Failed to initialise Synapsis DB: {e}")
        import sys

        sys.exit(1)

    mcp.run()


if __name__ == "__main__":
    main_server()
