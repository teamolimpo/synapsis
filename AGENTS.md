# AGENTS.md — Synapsis Memory Discipline for Grok Build

This project uses **synapsis** (the MCP server registered in `.grok/config.toml`) as the single source of durable, structured, cross-agent memory.

**Core rule**: Use durable structured memory (synapsis) **instead of** stuffing everything into the context window. **Handoff before you return control**.

## Mandatory Workflow (see GROK.md for full details)

1. **Start / Resume**
   - Use `search_tool` (query containing "synapsis") to discover tools if needed.
   - Then `use_tool` with **qualified names** only: `synapsis__session`, `synapsis__task`, `synapsis__hf`, `synapsis__search`, `synapsis__consolidate`, etc.
   - `synapsis__session(act="init", topic="...", resume=true, tids=[...])`

2. **Track + Observe**
   - Create tasks early with `synapsis__task(act="create", ...)`.
   - Record progress with `synapsis__session(act="observe", type="decision|result|note|...", content="...", entities=[...], tref="T-XXX")`.

3. **Formal Handoffs (non-negotiable for significant work)**
   - Every subagent, major decision, completed piece, or handoff to another agent **must** produce a `synapsis__hf(act="new", ...)`.
   - Handoffs are written to `Library/Handoff/YYYY/MM/DD/` as durable .md files (with optional Wiki contribution section).
   - Immediately after: `synapsis__task(act="log", tid=..., evt="handoff_ref", hpath=...)`.

4. **Recall**
   - Primary tool: `synapsis__search(query="...", scope="auto|tasks|hf|timeline|knowledge|...", l=1|2|3, tk=...)`.
   - Prefer targeted search (l=2) over dumping full session context.

5. **Close the loop**
   - Update tasks.
   - `synapsis__session(act="summarize")`
   - `synapsis__consolidate(auto=true, dry=true)` — review, then apply if sensible.

6. **Escalation (T-GH-001 – solo-in-GH-repo "act as if we were many")**
   - Problems (blk, hf st=fail/hold+devi, critical errors, non-trivial workarounds, hygiene pain) must be externalized according to the level in `.synapsis/config.yaml` (`escalation.problem_reporting`: off | hf | hf+notify | hf+gh).
   - The `synapsis problem ...` CLI (or direct call to `tools.synapsis.report.report_problem`) creates a structured GitHub Issue (using the synapsis-problem template when present) when level=`hf+gh`.
   - Always log the GH issue ref back into the task (and observe).
   - See `.synapsis/escalation-policy.md` and the comparative handoff hf-6524 for the exact rules and workpad-style body convention.
   - This forces visibility and analysis instead of silent workarounds.
   - `synapsis__admin(act="stats")`

   **How to escalate from your agent (explicit)**:
   ```python
   from tools.synapsis.report import report_problem

   report_problem(
       title="Task T-FOO-123 blocked on X",
       body="Detailed symptom...",
       tref="T-FOO-123",
       sid="ses_xxx",  # if you have the current session id
       # Preferred structured form (aligns with policy + template):
       error="Exact error / deviation / block description",
       workaround="What was tried (if any)",
       analysis="What needs investigation or next action",
   )
   ```
   Use `level=...` override only for testing. The auto paths (task blk, hf devi/bad-st, consolidate hygiene) already call this internally.

   **Automatic triggers (current state)**:
   - `task` update to `blk` (in server.py)
   - `hf_new` when `devi` non-empty or `st` in (fail, hold, kill)
   - `consolidate` when auto-triggered on high unconsolidated/old obs or contradictions detected
   See the call sites in `tools/synapsis/server.py` and `hf.py` for the exact conditions and data passed.

## Git Workflow Discipline (companion to memory / synapsis discipline)
**Core rule**: Before *any* modification (code, docs, config, even trivial one-liners or typos) create a dedicated branch. **Never work directly on main**. All changes land on main via Pull Request after CI gates and review (self-review artifact acceptable for solo rigor).

See the auto-loaded rule file: `.grok/rules/01-git-workflow-discipline.md` (created as follow-up to T-ANALISI-001 / proteo hf-ca58 research + the explicit anti-pattern documented in hf-8565 during T-GH-001).

This is now mandatory process alongside the synapsis handoff/task discipline. SOP is our guide.

## Project Skills (recommended way to use synapsis)

Use the project skills instead of raw tool sequences when possible:
- `/handoff` — structured handoff flow with recall + proper linking.
- `/mem` or `/synapsis` family (search, health, init, etc.) — see `.grok/skills/`.

## References

- Detailed patterns, tool-by-tool guidance, examples, token efficiency rules: **[GROK.md](./GROK.md)**
- Handoff files + curated knowledge: `Library/`
- Hot DB: `.synapsis/synapsis.db` (gitignored)
- Synapsis source: `tools/synapsis/`
- CLI for maintenance: `uv run python -m tools.synapsis --help`

**Update this file and GROK.md together** when the memory patterns evolve.

This file is the canonical project rules entry point (standard AGENTS.md mechanism). GROK.md contains the expanded operational manual.