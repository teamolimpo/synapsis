---
name: synapsis
description: >
  General synapsis memory operations: init/resume session, search/recall, observe progress, create or update tasks, run health/stats/consolidate, and end-of-work hygiene.
  Primary entry point for most memory interactions in this project. Prefer the more specific /handoff skill for formal handoffs.
when-to-use: "Use when the user asks to init memory, search synapsis, record an observation, create a task, check memory health, consolidate, or any general synapsis__* operation. Also for '/mem', '/memory', '/synapsis', 'recall', 'synapsis health'."
argument-hint: "[init|search|observe|task|health|consolidate|stats] <query or description>"
---

# Synapsis Memory Skill

Encapsulates the recommended patterns from GROK.md for working with the synapsis MCP (sessions, tasks, observations, search, consolidate, handoffs, deliverables).

**Always** discover via `search_tool` first if the qualified tool names are not fresh in context, then use `use_tool` with names like `synapsis__session`, `synapsis__search`, `synapsis__task`, etc.

## Common Sub-Commands (argument driven)

### init / start / resume
```
use_tool synapsis__session {
  "act": "init",
  "topic": "<clear description of the work>",
  "resume": true,
  "tids": ["T-XXX", ...]
}
```
Returns `sid`, layered context hints, health info.

### search / recall (primary memory tool)
```
use_tool synapsis__search {
  "query": "keywords OR entities OR T-XXX",
  "scope": "auto" | "tasks" | "hf" | "timeline" | "observations" | "knowledge" | "all",
  "l": 1 | 2 | 3,
  "n": 5-12,
  "tk": <token budget if known>,
  "since": "2026-06-01"
}
```
- `l=1`: cheap counts
- `l=2`: human readable snippets (sweet spot)
- `l=3`: full payloads

### observe (lightweight progress)
```
use_tool synapsis__session {
  "act": "observe",
  "sid": "<sid>",
  "type": "decision" | "result" | "note" | "delegation" | "handoff" | "user_message",
  "content": "...",
  "agent": "Grok" | "Poros" | "...",
  "entities": ["Entity", "Library", "T-XXX"],
  "tref": "T-XXX",
  "hpath": "Library/Handoff/..."
}
```

### task management (use short forms for token savings)
Create:
```
use_tool synapsis__task { "act": "c", "desc": "...", "prio": "high|medium|low", "parent": "T-YYY", "tags": ["tag1"], "owner": "Poros" }
```

Update / log (hottest path — every handoff does this):
```
use_tool synapsis__task { "act": "u", "tid": "T-XXX", "sts": "prog|done|blk", ... }
use_tool synapsis__task { "act": "l", "tid": "T-XXX", "evt": "hr|dec|note", "details": "...", "hpath": "..." }
```
Note: `"sts"` (not "status") and short `evt` like "hr" for handoff_ref are the canonical token-efficient forms.

Query / summary:
```
use_tool synapsis__task { "act": "q", "status": "pend", "owner": "Poros", "evts": true }
use_tool synapsis__task { "act": "sum" }
```

See "Token-efficient usage" section below for the full short-form vocabulary.

### health, stats, consolidate (hygiene)
```
use_tool synapsis__admin { "act": "stats" }
use_tool synapsis__admin { "act": "health", "cmd": "quick" }

use_tool synapsis__consolidate { "auto": true, "dry": true }   # review first
# then without dry if the patterns look good

use_tool synapsis__session { "act": "summarize", "sid": "...", "lv": 1 }
```

### deliverables (d_set / d_get)
Use when you produce an important artifact you want to reference immutably:
```
use_tool synapsis__d_set { "p": "/absolute/or/relative/path" }
use_tool synapsis__d_get { "h": "<hash from d_set>", "l": 2 }
```

## Recommended Closing Sequence (end of significant work)
1. `synapsis__session(act="summarize")`
2. `synapsis__consolidate(auto=true, dry=true)` — inspect result
3. `synapsis__admin(act="stats")`
4. If volume is high: consider compress via CLI or `synapsis__session(act="compress", ...)`

## Best Practices Encoded Here
- Always link with `tref`, `hpath`, `entities`.
- Default to `l=2` + targeted scopes.
- Use `dry=true` on consolidate, compress, admin mutations.
- Handoff files live on disk under Library/ — search finds them via the `hf` scope.
- Prefer this skill (or the dedicated `/handoff` skill) over raw long JSON sequences.

See full reference and examples in [GROK.md](../../GROK.md) and the synapsis source under `tools/synapsis/`.

## Token-efficient usage (short canonical forms)

To minimize tokens on the input side of frequent calls, synapsis accepts (and prefers) short forms. Long forms are normalized for backward compat.

**Task tool (`act` / `sts` / `evt` / status):**
- act: c (create), q (query), u (update), l (log), sum, exp, z (compress)
- sts / status: pend, prog (in_progress), done, blk, x (cancelled), stby
- evt: hr (handoff_ref — use on every formal handoff), dec, dv, sc, note

**Session observe `type`:**
- dec, del, res, note, hf (handoff), um (user_message), sys

**hf `act`:**
- n (new), g (get)

**Session `act`:**
- i (init), o (observe), ctx (context), sum (summarize), z (compress)

Always run `search_tool` then `use_tool` with qualified names. Use the short forms in production agent loops for best token economics. The server normalizers live in `server.py` (_norm_* functions) and the short values are now the .value in `models.py` enums.

This is the continuation of the `sts` optimization.