---
name: handoff
description: >
  Create a formal synapsis handoff (synapsis__hf) for any significant work, decision, subagent output, or deliverable.
  Always performs recall first, produces a dated Library/Handoff file, logs the handoff_ref on the related task(s), and records an observation.
  Use when the user says "handoff", "hand off", "/handoff", or at the end of any non-trivial piece of work before returning control.
when-to-use: "Use for mandatory structured handoffs in this synapsis-memory project. Trigger on 'handoff', 'produce handoff', 'before returning control', or end of sub-work."

**Important**: Before creating a handoff, ensure your private vault is mounted with the external symlink (Library -> ~/synapsis-vault). The simplest commands are:
  bash scripts/vault-mount.sh
or
  synapsis vault mount
The guard (ensure_vault_mounted) will give you this exact instruction if you forget.
argument-hint: "<title> [tref:T-XXX] [type:handoff|decision|research] [body:... or will prompt]"
---

# Handoff Skill (synapsis discipline)

You are executing the **mandatory handoff protocol** for the synapsis memory layer.

**Never** just narrate a handoff. You **must** perform the real tool calls using `search_tool` then `use_tool` with the fully qualified `synapsis__*` names.

## Step 0: Tool Discovery (if not already known)
If you have not confirmed the synapsis tools in this session:
1. Call `search_tool` with query containing "synapsis" (or "hf" / "handoff").
2. Note the qualified names: `synapsis__hf`, `synapsis__task`, `synapsis__session`, `synapsis__search`.

## Step 1: Quick Recall (always)
Before writing anything new, search for context:
- `use_tool synapsis__search {"query": "<relevant keywords from title/description>", "scope": "auto", "l": 2, "n": 6}`
- Optionally also `scope: "hf"` or `scope: "tasks"`.

This prevents duplicate handoffs and surfaces related prior work / entities.

## Step 2: Resolve tref (task link)
- If the user provided a `tref` (e.g. T-MEM-007 or T-XXX), use it.
- Otherwise, use recent tasks or create one: `synapsis__task(act="create", desc="...", prio="medium", tags=["handoff"])`.
- Log handoffs against the task(s) they advance.

## Step 3: Build and emit the handoff
Call:
```
use_tool synapsis__hf {
  "act": "new",
  "type": "handoff" | "decision" | "research" | "deliverable",
  "title": "<clear, searchable title>",
  "body": "<detailed markdown content>\n\n## Wiki\nkind: decision|learning|architecture\npath: <category>/<slug>\ntitle: <short>\nsummary: <one paragraph>\ntags: [tag1, tag2]\nconfidence: CONFIRMED",
  "agent": "Grok" | "Poros" | "<subagent-name>",
  "tref": "T-XXX",
  "st": "done" | "in_progress",
  "prio": "high" | "medium" | "low",
  "note": "optional extra context",
  "refs": ["optional related hf- or paths"]
}
```

**Wiki section in body** (strongly encouraged for durable knowledge):
If the content represents reusable knowledge, include a `## Wiki` block at the end of `body`. The server will extract it into `Library/Wiki/`.

## Step 4: Log the handoff on the task
Immediately after the hf tool succeeds:
```
use_tool synapsis__task {
  "act": "log",
  "tid": "T-XXX",
  "evt": "handoff_ref",
  "details": "Produced <hf-ref> — <one-line summary>",
  "hpath": "Library/Handoff/<year>/<mm>/<dd>/<full-filename-from-result>.md"
}
```

## Step 5: Observation (lightweight timeline entry)
```
use_tool synapsis__session {
  "act": "observe",
  "sid": "<from init or previous>",
  "type": "handoff",
  "content": "Handoff: <title> (<hf-ref>)",
  "agent": "...",
  "entities": ["relevant", "entities"],
  "tref": "T-XXX",
  "hpath": "Library/Handoff/..."
}
```

## Step 6: Close hygiene (recommended)
- `synapsis__session(act="summarize", sid=..., lv=1)`
- `synapsis__consolidate(auto=true, dry=true)` (review output; apply without dry only if sensible)

## Output to user
After success, report clearly:
- The `ref` of the handoff (e.g. hf-a1b2c3)
- Full path under `Library/Handoff/...`
- Whether a Wiki contribution was created
- The task(s) it was logged against
- Link the handoff file path so the user can open it.

## Rules specific to this skill
- Always do Step 1 (recall) before writing.
- Always produce a **real** `synapsis__hf(act="new")` — never simulate.
- Always follow with the `task log` using the returned `hpath`.
- Prefer including a `## Wiki` section when the content has long-term value.
- Use `dry: true` on any dangerous follow-ups (consolidate, compress).
- Respect token layers (`l`, `tk`) on search.

This skill encodes the "handoff before you return control" discipline from GROK.md.