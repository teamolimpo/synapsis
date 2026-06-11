# synapsis

**Unified durable team memory MCP + multi-provider LLM client**, extracted as a focused, reusable package.

Synapsis is a standalone memory layer designed for agentic workflows and Grok Build. It was originally extracted from a larger internal project.

Designed to work great with **Grok Build** (and other agent harnesses that speak MCP).

## Why this exists

When you have multiple specialist agents (or subagents), the hard part is not creating the agents — it's making them hand work to each other reliably, keep shared context, audit what happened, and build up durable knowledge without losing it between sessions.

Synapsis solves that with:

- **Session + observation timeline** with smart token compression and multi-level summarization.
- **Task tracking** with state machine, events, and parent/child relationships.
- **Handoff protocol** (`synapsis__hf`) — every significant piece of work produces a structured, searchable handoff file (with optional Wiki contribution).
- **Unified search** across everything (observations, tasks, handoffs, knowledge/wiki chunks) with FTS5 + optional hybrid/embedding modes.
- **Knowledge / Wiki** layer (chunks + search) that handoffs can feed automatically.
- All backed by a single SQLite DB (`.synapsis/synapsis.db` by default — local, low-latency operational store) + handoff files and curated knowledge under `Library/`. The DB path is overridable via `SYNAPSIS_DB_PATH`.

**Library/** is the mount point for the **private vault** (teamolimpo/synapsis-vault). It is required for full durable handoffs and private knowledge (tensor-mill members). External contributors only cloning the public repo will not have it. See the "Tensor-mill / full memory setup" section below.

Plus a high-quality **LLM client** (`tools/llm`) that lets you call Grok (including multi-agent variants), Gemini, OpenRouter, etc. from your own Python code, skills, or additional MCPs.

## Quick Start with Grok Build

```bash
# Clone the focused package
git clone https://github.com/teamolimpo/synapsis.git
cd synapsis

uv sync

# Run Grok from inside this directory
grok
```

Grok will automatically pick up the project-scoped MCP from `.grok/config.toml`.

In the TUI:
- Type `/mcps` (or use the MCP modal) — you should see **synapsis**.
- The tools will be namespaced: `synapsis__search`, `synapsis__session`, `synapsis__task`, `synapsis__hf`, `synapsis__d_set`, `synapsis__d_get`, etc.

## Tensor-mill / full memory setup (private vault)

The public repo is the **environment** (tools, rules, skills, public SOPs).

The private content (all handoffs, Wiki, projects, assets, private SOPs) lives in a separate repo (`teamolimpo/synapsis-vault`) that you symlink as `Library/`.

**Comando semplicissimo** (after cloning both repos):

```bash
cd synapsis          # the public clone
uv sync

# One of these two (both do the external symlink + prepare .synapsis/)
bash scripts/vault-mount.sh
# or the integrated command:
synapsis vault mount
```

You are now **subito ready** with your full work tool (durable `/handoff`, private search, projects/, etc.).

- `bash scripts/vault-check.sh` / `synapsis vault check`
- `bash scripts/vault-doctor.sh` for diagnostics
- `bash scripts/vault-unmount.sh` to remove the symlink safely

See also `scripts/` for the other helpers and the plan in `plans/vault-setup-automation-001.md`.

## Recommended Integration with Grok Build (2026+)

Instead of raw long `search_tool` + `use_tool synapsis__xxx {...}` sequences, use the **project skills**:

- `/handoff <title> ...` — the mandatory structured handoff flow (recall first, proper hf + task log + observe, Wiki contribution support).
- `/synapsis ...` (or `/mem`) — general memory ops: init, search/recall, observe, task mgmt, health, consolidate, stats, hygiene.

These live in `.grok/skills/` (version controlled) and appear in the slash menu.

**Project rules** are now properly loaded via the standard mechanism:
- `AGENTS.md` (short, canonical entry point) + any `*.md` under `.grok/rules/`
- `GROK.md` remains the detailed operational manual (tool-by-tool, examples, token strategies, full handoff discipline). The AGENTS.md points to it.

**Automatic hygiene** via project hooks (`.grok/hooks/synapsis-hygiene.json`):
- On `Stop`, `PreCompact`, `SessionEnd`: runs `synapsis hygiene` (dry consolidate + stats).
- First time you open the project with hooks you must trust it (or use the `/hooks` modal).

Basic usage example (tell the agent):

> "Initialize a session with topic 'Porting handoff protocol' and then observe this decision: we chose synapsis as the package name."

Or simply: "Use /handoff for this piece of work."

Typical flow the memory expects:
1. `/synapsis init "topic..."` (or raw session init)
2. Do work, record with observations or tasks
3. `/handoff "Clear title" tref:T-XXX ...` (or raw synapsis__hf + log)
4. Later recall with `/synapsis search "..."` or targeted `synapsis__search`

Handoff files land in `Library/Handoff/YYYY/MM/`. Wiki contributions (from handoffs) land in `Library/Wiki/`.

See:
- `AGENTS.md` (loaded project rules)
- `GROK.md` (full patterns and discipline)
- `.grok/skills/handoff/SKILL.md` and `synapsis/SKILL.md`
- `uv run python -m tools.synapsis --help` (CLI for maintenance/hygiene)

## The LLM client

```python
from tools.llm.config import get_api_key
from tools.llm.providers.grok import GrokProvider

provider = GrokProvider(get_api_key("grok"))
resp = provider.chat("Summarize the Synapsis handoff protocol", model="grok-4-1-fast-non-reasoning")
print(resp.text)

# Or multi-agent Grok
resp = provider.chat("Deep research on memory patterns", model="grok-4.20-multi-agent-0309", agent_count=8)
```

It also supports Gemini, OpenRouter, image generation on supported models, and stateful chat sessions via the Responses API.

Use it whenever you want to call a non-default model from inside tools you write while using Grok Build as the main orchestrator.

## Project layout (kept close to original for "as-is" fidelity)

```
synapsis/
├── .grok/
│   └── config.toml          # MCP registration for Synapsis
├── .synapsis/               # Local low-latency runtime memory (gitignored)
│   └── synapsis.db          # The hot operational SQLite store (sessions, tasks, observations, FTS5, ...)
├── tools/
│   ├── common/
│   │   └── paths.py         # project_root + symlink-aware resolution (Library + .synapsis)
│   ├── synapsis/            # The memory MCP (server, store, hf handoffs, search, etc.)
│   └── llm/                 # Multi-provider LLM client
├── Library/                 # Gitignored — curated/static/vault content (Handoff + Wiki)
│   ├── Handoff/
│   └── Wiki/
├── pyproject.toml
└── README.md
```

The `tools/` layout is preserved so that all internal imports (`from tools.common.paths`, `from tools.synapsis.models`, etc.) continue to work without modification. You can still run:

```bash
uv run python -m tools.synapsis
```

exactly as before.

## .synapsis vs Library (hot operational memory vs curated/vault content)

- **`.synapsis/`** (default): low-latency local runtime store. Contains:
  - `synapsis.db` (plus WAL/SHM) — sessions, observations, tasks, entities, FTS5, knowledge chunks, etc.
  - `config.yaml` — optional local operational configuration for the whole synapsis instance (e.g. what to index under `knowledge.include` / `knowledge.exclude`).
  Fast local I/O by design. Fully gitignored.
- **`Library/`**: the mount point for the private vault (teamolimpo/synapsis-vault). Contains all handoffs, Wiki, projects/, assets, and private SOPs. Required for tensor-mill members. Created/maintained with the simple `vault-mount` commands above. Gitignored in the public repo (the symlink entry itself is never committed).

The split exists so that the very active DB (frequent small writes + searches) stays on fast local storage, while you can still keep handoffs and curated knowledge in a separate, possibly remote/slower vault.

`tools/common/paths.py` provides `resolve_absolute()` / `resolve_relative()` for symlink handling (mainly useful for Library parts).

Override the DB location anytime with the `SYNAPSIS_DB_PATH` environment variable (e.g. to point back at an old `Library/System/Poros/synapsis.db` or to a shared location). Handoff file location is currently under Library (see `tools/synapsis/hf.py`); this may evolve later.

## Status & Relationship to original project

This is a focused extraction of the two strongest reusable components:

- Synapsis (the memory/handoff/knowledge system)
- The LLM multi-provider client

Many of the concepts (mandatory handoffs, structured memory, quality gates) translate well to agentic setups and subagent coordination patterns.

## Contributing / Philosophy

If you extend this, try to keep the "handoff before you return control" spirit and make heavy use of the durable memory instead of stuffing everything into the agent's context window.

The handoff protocol and memory discipline are documented in `GROK.md` and `AGENTS.md`.

## License

MIT (same as the original extraction source).

---

Made to be useful with Grok Build (and any MCP-speaking agent system). Feedback and improvements welcome — especially around making the memory layer even more powerful when combined with subagents and worktree isolation.
