# GROK.md — Synapsis Memory Usage for Grok Build

This file teaches Grok Build (and any subagents) how to use the **synapsis** MCP server effectively.

Synapsis is the unified durable memory layer for this project:
- Session + observation timeline with token compression
- Task tracking with state machine and events
- Mandatory structured **handoffs** (`synapsis__hf`)
- Unified search across everything (observations, tasks, handoffs, knowledge/wiki, entities, timeline)
- Knowledge / Wiki contributions from handoffs
- Content-addressed deliverables (`d_set` / `d_get`)
- Auto-consolidation into memory layers

**Core philosophy**: Use durable structured memory (synapsis) instead of stuffing everything into the context window. **Handoff before you return control**.

All data lives in:
- `.synapsis/synapsis.db` (hot operational SQLite + FTS5 — gitignored)
- `Library/Handoff/YYYY/MM/DD/` (structured handoff .md files — inside the private vault via the Library symlink)
- `Library/Wiki/` (curated contributions extracted from handoffs — private vault)

## Tool Discovery in Grok Build

Synapsis tools are exposed as an MCP server. Always discover first if unsure:

1. Call the built-in `search_tool` tool with query containing "synapsis" (or a specific tool name).
2. Then call the discovered tools using the built-in `use_tool` with the **fully qualified name** (e.g. `synapsis__session`, `synapsis__hf`).

Qualified names (confirmed via `search_tool`):
- `synapsis__session`
- `synapsis__task`
- `synapsis__hf`
- `synapsis__search`
- `synapsis__consolidate`
- `synapsis__admin`
- `synapsis__d_set`
- `synapsis__d_get`

Responses are often JSON (sometimes key-compressed for token efficiency). Many operations support `dry: true` (default where dangerous).

## Recommended Overall Workflow

Typical cycle for any non-trivial work:

1. **Start / Resume**
   - `synapsis__session(act="init", topic="Clear topic describing the work", resume=true, tids=[...])`
   - This returns the active `sid`, layered context, and health/consolidation hints.

2. **Track Work**
   - Create tasks early: `synapsis__task(act="create", desc="...", prio="high|medium|low", parent=..., tags=[...])`
   - ID format is `T-AREA-NNN` (auto-generated or explicit with validation).

3. **Capture Progress (lightweight)**
   - `synapsis__session(act="observe", sid=..., type="decision|delegation|result|note|handoff|...", content="...", agent="YourName", entities=["Entity1"], tref="T-XXX", hpath="...")`
   - Observations are the raw timeline. Always include `entities` when relevant.

4. **Formal Handoffs (mandatory for significant pieces)**
   - Use `synapsis__hf(act="new", ...)` for anything you would hand to another agent or want durable + searchable.
   - Handoffs write real files under `Library/Handoff/` and can auto-contribute to `Library/Wiki/`.
   - Log the handoff on the related task: `synapsis__task(act="log", tid=..., evt="handoff_ref", details="...", hpath=...)`.
   - Update task status as you go.

5. **Recall & Context Building**
   - Primary tool: `synapsis__search(query="...", scope="auto|tasks|observations|timeline|hf|knowledge|...", l=1|2|3, n=5-10)`
   - `l=1`: counts + domains (cheap)
   - `l=2`: readable snippets per domain (most common)
   - `l=3`: full structured payload
   - Use `tk` param to auto-select layer based on token budget.
   - Alternative for a specific session: `synapsis__session(act="context", sid=..., l=2)`

6. **Close the Loop**
   - Update tasks to completed / blocked etc. (respects state machine; auto-completes parents when children finish).
   - Emit final handoff if needed.
   - `synapsis__session(act="summarize", sid=..., lv=1|2|3)`
   - `synapsis__consolidate(auto=true, dry=true)` — review, then run without dry when appropriate.
   - Optionally compress old data.

## Tool-by-Tool Guidance

### synapsis__session
Multi-purpose lifecycle tool.

**Key acts**:
- `init`: Always the starting point. `resume=true` reuses the last active session and merges `tids`.
- `observe`: The main way to record decisions, results, notes. Strong typing + automatic entity linking.
- `context`: Pull layered memory for a known `sid` (good for injecting into a subagent prompt).
- `summarize`: Compress recent observations into a `summaries` row.
- `compress`: Warm (level 1) or cold (level 2) compression of observations + task events. Use `dry=true` first.
- `tasks`: List tasks linked to this session.

**Observation types** (exact):
`decision`, `delegation`, `result`, `note`, `handoff`, `user_message`, `system`

**Best practices**:
- Pass `entities` liberally — they enable powerful cross-referencing.
- Link to tasks with `tref`.
- Link to handoff files with `hpath` when applicable.
- Use `tkdc` / `tkrd` when you have token counts from discovery/read phases.

### synapsis__task
Full task state machine in one tool.

**Important acts**:
- `create`: Provide `desc`. Use `parent` for hierarchy. Tags must be single words (no spaces).
- `query`: Rich filters (`status`, `owner`, `prio`, `tag`, `search`, `since`, `tid`, `evts`).
- `update`: Change status (prefer short forms: act="u", sts="prog|done|blk" etc. for token discipline). Validates transitions via state machine. Auto-completes parent when all children are done. See "Token-efficient usage" in the synapsis skill docs.
- `log`: Append events (`handoff_ref`, `note`, `decision`, `deviation`, `status_change`, `created`). Always log handoff refs.
- `summary`: Aggregates (by_status, by_priority, wip_current, oldest_pending). Defaults to owner "Poros".
- `export`: Full YAML dump (useful for backup/migration).
- `compress`: Compress old task events.

**ID convention**: `T-<AREA>-<NNN>` (e.g. `T-MCP-001`, `T-MEMORY-012`).

**Best practices**:
- Create tasks early.
- Log every handoff and important decision against the task.
- Use `owner` to scope (default "Poros"; tests sometimes use "GrokTest").
- Query with `evts: true` when you need history.

**Escalation (see also AGENTS.md and .synapsis/escalation-policy.md)**:
When a task goes `blk`, a handoff has `st=fail/hold` + `devi`, or you apply a non-trivial workaround, escalate according to the level in the config (default `hf+gh`). The reporter will create a GitHub Issue with a structured "workpad" body and log the URL back. This is the mechanism that lets a solo human + agents "act as if we were many".

**Explicit escalation from agent code** (preferred for non-trivial workarounds or custom cases):
```python
from tools.synapsis.report import report_problem

report_problem(
    title="...",
    body="...",
    tref="T-XXX-001",
    sid="ses_...",  # when available
    error="What exactly failed / the deviation",
    workaround="What you tried (optional)",
    analysis="What should be investigated next (optional)",
)
```
Auto paths (blk, devi handoffs, consolidate pain) already call this for you. See `tools/synapsis/report.py` and the triggers in `server.py` / `hf.py`. Use the CLI `synapsis problem "title" --tref T-XXX` for manual/one-off cases.

### synapsis__hf (Handoffs — the most important discipline)
`act="new"` or `act="get"`.

**For new handoffs (required fields)**:
- `type`, `title`, `body`, `agent`
- Strongly recommended: `tref` (link to task), `st` ("done" | "in_progress" | ...), `prio`
- Optional but powerful: `note`, `refs`, `devi` (deviations), `wiki` contribution via body

**Body format for Wiki contribution** (optional but encouraged for durable knowledge):
```markdown
... normal handoff content ...

## Wiki
kind: decision
title: Short title
path: decisions/some-topic
summary: One-paragraph summary (max ~300 chars)
tags: [tag1, tag2]
sources: [link or ref]
confidence: CONFIRMED
```

Handoff files are written as dated Markdown with clean YAML frontmatter under `Library/Handoff/`.

**act="get"**: Retrieve by `ref` (e.g. "hf-a1b2"). Use `tk` for truncation.

**Best practices**:
- Every subagent handoff, major decision, completed piece of research, or external deliverable should produce a handoff.
- After writing a handoff, immediately log it on the related task(s).
- Search for past handoffs with `synapsis__search(..., scope="hf")`.

### synapsis__search (your primary memory tool)
Use this constantly for recall.

**Scopes**: `auto` (recommended), `all`, `tasks`, `observations`, `timeline`, `hf`, `knowledge`, `entities`, `session`, etc.

**Layers** (`l`):
- 1: High-level counts per domain + suggestion to use higher layer.
- 2: Human-readable grouped snippets (default sweet spot).
- 3: Full raw results (use when you need every detail).

**Token-aware usage**: Pass `tk` (token budget) — the tool will choose a sensible layer.

**Filters**: `ref`, `since` (ISO), `n` (max results).

**Examples** (via use_tool):
- Broad recall: `{"query": "memory compression", "scope": "auto", "l": 2}`
- Specific task: `{"query": "T-MCP-001", "scope": "tasks", "l": 3}`
- Recent decisions: `{"query": "decision", "scope": "timeline", "since": "2026-06-01"}`

Prefer search over dumping full session context.

### synapsis__consolidate
Distills raw observations into structured `memory_layers` (context, caveat, learning, etc.).

- `auto: true` (default): Only runs real work if triggers are met (>20 unconsolidated, old data, high volume). Safe to call often.
- `dry: true` (default): Shows what would happen (patterns, contradictions, top entities).
- Explicit mode (`auto: false`): Forces full pass.

Call at natural boundaries (end of a sub-task, before compaction in Grok Build, end of day). Review the dry output first.

### synapsis__admin
Maintenance and diagnostics.

Useful acts:
- `stats` (scope `all` | `basic` | `health`): Current state, token economics, compression ratios, active sessions.
- `health` (cmd `quick` | full): Quick DB sanity.
- `orphan`: Find orphaned tasks.
- `vacuum`: Reclaim space + reindex.
- `domain`: Enable/disable domains (e.g. `knowledge`).
- `index`: Manage knowledge chunk index (update/rebuild/clean) — only if the knowledge_base module is available.
- `checkpoint`: Create/restore named checkpoints.

Run `admin(act="stats")` periodically so you know the health of the memory store.

### synapsis__d_set / synapsis__d_get (Deliverables)
Lightweight content-addressable registry for important output files.

- `d_set(p: "/absolute/or/relative/path/to/file")` → returns `{"h": "hash"}`
- `d_get(h: "hash", l: 1|2|3)` → l=1 meta only, l=2 meta + ~500 chars, l=3 full content.

Use when you produce a report, patch, design doc, or any artifact you want to reference immutably later (put the hash in handoffs and observations).

**Live example from this workspace**:
- `d_set` on README.md produced hash `099368d6`.
- `d_get` with l=2 returns path + beginning of content.

## Integration with Grok Build Features (improved surface)

- **Project rules**: The canonical loaded file is now `AGENTS.md` (standard mechanism). It points to this `GROK.md` for details. Additional rules can live in `.grok/rules/*.md`.
- **Skills (strongly recommended)**: Use the project skills instead of raw sequences:
  - `/handoff ...` — full recall + formal hf + task log + observe + hygiene (see `.grok/skills/handoff/SKILL.md`).
  - `/synapsis ...` (or `/mem`) — init, search, observe, task, health, consolidate, hygiene (see `.grok/skills/synapsis/SKILL.md`).
  These appear in the slash menu and encode the correct `search_tool` → `use_tool qualified-name` dance + linking + dry-run discipline.
- **Subagents / plan / implement / review loops**: Every subagent should receive relevant `synapsis__search` results or a `session(context)` slice. Every subagent **must** produce at least one handoff (`/handoff` or raw `synapsis__hf`) before returning control.
- **Hooks**: `.grok/hooks/synapsis-hygiene.json` runs `synapsis hygiene` (dry consolidate + stats) on `Stop`, `PreCompact`, and `SessionEnd`. (Project hooks require one-time trust.)
- **CLI hygiene**: `uv run python -m tools.synapsis hygiene` (or `hygiene --apply`) — useful from hooks, manual, or skills.
- **Grok Build's own memory** (`/memory`, `/flush`, `/dream`): Orthogonal. Use Grok's for cross-project personal notes; use synapsis for project/team/agent durable handoff knowledge.
- **Todo lists**: Use Grok's `todo_write` for the current turn's plan, but record durable outcomes in synapsis tasks + handoffs.

## Token discipline on synapsis interfaces

The tool surface (especially `synapsis__task` and `synapsis__session`) uses short canonical forms for frequent strings to reduce input token cost on agent loops (following the `sts` precedent).

See the dedicated section in `.grok/skills/synapsis/SKILL.md` ("Token-efficient usage") for the vocabulary (act="u"/"l", sts="prog", evt="hr", observe type="hf"/"um", etc.).

Long forms are still accepted (normalized server-side). Update your recurring patterns to the shorts for measurable savings on handoff-heavy or high-observe workloads. Output responses are already compressed via _JMAP.

## Token Efficiency & Layers

- Default to `l=1` or `l=2` in search and session(context).
- Use `dry: true` on consolidate, compress, and any admin mutation.
- Let `tk` parameter auto-select layers when you have a budget.
- After heavy observe or tool use, run summarize + consolidate to keep hot data small.
- Handoff files are on disk (higher latency, vaultable) — search will find them via the hf domain.

## Current Project Conventions (this workspace)

- Default agent/owner: "Poros" (override with `agent` or `owner` when it makes sense, e.g. subagent names or "GrokTest" during experiments).
- Test data may exist under other owners — always use search or explicit filters.
- Knowledge domain indexing (chunks) may be available via admin(index) but is optional.
- Library/ is the mount point for the private vault (required for tensor-mill). It is intentionally not present in public-only clones. Use `synapsis vault mount` or `bash scripts/vault-mount.sh` (the "comando semplicissimo") to become ready. See the public README "Tensor-mill / full memory setup" section.

## Common Patterns & Examples

**Start a new focused effort**:
```
search_tool (to confirm tools)
use_tool synapsis__session {"act":"init","topic":"Porting handoff protocol to standalone package","resume":true}
use_tool synapsis__task {"act":"create","desc":"Define clean MCP surface for handoffs","prio":"high"}
```

**Record a decision + link**:
```
use_tool synapsis__session {"act":"observe","sid":"...","type":"decision","content":"We will use Library/ for durable handoffs and .synapsis/ for the hot DB","agent":"Grok","entities":["Library","Handoff",".synapsis"],"tref":"T-MEM-003"}
```

**Formal handoff at end of sub-work**:
```
use_tool synapsis__hf {"act":"new","type":"handoff","title":"Synapsis MCP surface design","body":"... detailed findings ...\n\n## Wiki\nkind: decision\npath: architecture/synapsis-mcp\nsummary: ...","agent":"Grok","tref":"T-MEM-003","st":"done","prio":"high"}
use_tool synapsis__task {"act":"log","tid":"T-MEM-003","evt":"handoff_ref","details":"Produced hf-XXXX","hpath":"Library/Handoff/..."}
```

**Recall before starting something**:
```
use_tool synapsis__search {"query":"handoff protocol OR memory layers","scope":"auto","l":2,"n":8}
```

**End of session hygiene**:
```
use_tool synapsis__session {"act":"summarize","sid":"...","lv":1}
use_tool synapsis__consolidate {"auto":true,"dry":true}
# review output, then run again with dry:false if happy
use_tool synapsis__admin {"act":"stats"}
```

## Maintenance & Debugging

- `admin(act="stats")` — always your first diagnostic.
- `admin(act="health")`
- `admin(act="orphan")`
- `admin(act="vacuum")` when DB grows.
- If search feels stale: the FTS5 indexes are maintained via triggers; vacuum or checkpoint as needed.
- Check `~/.grok/logs/mcp/synapsis.stderr.log` (or project-local) if the MCP server has issues.

## Rules

- Never ignore the handoff discipline for work that crosses agent boundaries or needs to survive session compaction.
- Prefer `search` (l=2) over loading massive session contexts.
- Always link observations, handoffs, and task events to each other (tref, hpath, entities).
- Use dry-runs on any mutating or expensive operation.
- Keep Library/ content high-signal — it is meant to be curated/vaulted.
- **When editing public artifacts** (README, rules, code in tools/, public SOPs, .grok/ stuff, etc.) while the private vault is mounted: be deliberate. Use narrow `synapsis__search` or do private recall *after* the public change. Do not paste private hpaths, internal project names, or handoff excerpts into public commits, PRs, or comments. The guard and mount commands exist so insiders have full power without accidentally leaking context.

This file (`GROK.md`) is the detailed operational manual. The auto-loaded project rules entry point is `AGENTS.md` (which references this file). Skills in `.grok/skills/` provide the best day-to-day UX. Update AGENTS.md / GROK.md / the skills together when patterns evolve.

For the handoff protocol and memory discipline, see the dedicated sections in this file and `AGENTS.md`.
