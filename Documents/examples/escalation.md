# Escalation Examples (T-GH-001)

This file shows how to use the escalation / self-reporting mechanism so that agents (or the system) externalize problems instead of silently working around them.

See:
- `.synapsis/escalation-policy.md` (when + levels)
- `tools/synapsis/report.py` (the `report_problem` function)
- AGENTS.md / GROK.md sections on escalation
- `synapsis problem --help` (CLI)

## 1. Explicit call from agent code (recommended for non-trivial workarounds)

```python
from tools.synapsis.report import report_problem

result = report_problem(
    title="Task T-FOO-123 blocked on database migration",
    body="Migration failed with 'duplicate key' after 3 retries.",
    tref="T-FOO-123",
    sid="ses_abc123",  # pass current session if you have it
    # Structured fields (preferred – align with the issue template and policy)
    error="Duplicate key error on table 'events' during ALTER",
    workaround="Retried with exponential backoff and different batch size – still fails",
    analysis="Need to investigate unique constraint and data shape in prod vs staging. Create follow-up task to backfill or relax constraint.",
)

print(result)
# {
#   "title": "...",
#   "effective_level": "hf+gh",
#   "internal_logged": true,
#   "notified": true,
#   "issue_url": "https://github.com/.../issues/NNN"
# }
```

The function always does internal logging (task event + optional session observe). When level=`hf+gh` (the default for this repo) it also creates a GitHub Issue with the enriched workpad body and logs the URL back.

## 2. Using the CLI (manual / one-off)

```bash
synapsis problem "Task T-BAR-007 in failed state after handoff" \
  --tref T-BAR-007 \
  --body "handoff st=hold with devi: 'timeout on external API after 30s'"

# Override level for testing (does not touch real GitHub)
synapsis problem "Test escalation" --tref T-TEST-001 --level hf
```

## 3. Automatic triggers (you don't call these directly)

These are wired in the MCP server so that common "pain" points escalate without extra code:

- **Task blocked**: when you do `task(act="update", tid="T-XXX", sts="blk", note="...")` the server calls `report_problem` (with sid if you passed it on the call).
- **Handoff deviation**: `hf(act="new", ..., devi="reason", st="hold")` (or fail/kill) triggers escalation inside `hf_new`.
- **Consolidate hygiene pain**: when `consolidate(auto=True)` triggers on >20 unconsolidated / old obs, or when explicit consolidate detects contradictions.

See the exact conditions and data passed in:
- `tools/synapsis/server.py` (task update blk, consolidate hooks)
- `tools/synapsis/hf.py` (inside hf_new)

## 4. Structured body convention (workpad)

When an Issue is created it follows the sections from `.synapsis/escalation-policy.md` and the `synapsis-problem.yml` template:

- **Context**: tref, sid, git sha, etc.
- **Error / Deviation / Block**: exact symptom
- **Attempted workaround** (if any)
- **What needs to be analyzed / next action**

The reporter adds a small header and footer. This makes the GitHub Issue the visible "board" for the problem.

## Tips

- Always provide `tref` when you have one – it links the escalation back to the task.
- Pass `sid` when you are inside a session – this creates a loud `type=system` observation in the session timeline.
- For pure "I decided to do a non-trivial workaround" cases, prefer the explicit `report_problem` call so the decision is recorded with context.
- After escalation, the task event will contain something like `[escalation] level=hf+gh ... gh=https://.../issues/NNN`.
- Close the GitHub Issue only when root cause is understood and a real fix (or accepted permanent workaround with rationale) is in place. Link PRs with "Closes #N".

## Current automatic triggers (as of 2026-06)

- `task` status → `blk`
- `hf` creation with `devi` or `st` in (fail, hold, kill)
- `consolidate` auto trigger (high unconsolidated / old observations) or contradictions detected in explicit run

See the code for the exact strings passed as `error` / `analysis` etc. (they have been improved to produce nicer workpad bodies).

---

*This file was added as part of P2 #8 (Documentation & Examples) on T-CLI-002.*