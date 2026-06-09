# Synapsis Memory Layer — Project Rules (loaded via .grok/rules/)

This file is automatically loaded as part of project instructions (any *.md in .grok/rules/ is included).

See the root AGENTS.md for the high-level mandatory workflow.

Detailed operational guidance, tool-by-tool usage, token strategies, examples, and the full handoff protocol live in:

- GROK.md (root of repo)
- Library/Handoff/ (real produced handoff artifacts)
- tools/synapsis/ (implementation + tests)

Key invariants that must be followed in every session:

- Discover synapsis tools via the built-in `search_tool` (query ~ "synapsis"), then call them exclusively via `use_tool` using the **qualified** names (`synapsis__session`, `synapsis__hf`, etc.).
- Every non-trivial piece of work that crosses agent boundaries or should survive compaction **must** produce a formal handoff via `synapsis__hf(act="new")` + task log.
- Use the provided project skills (`/handoff`, `/synapsis`) when they apply — they encode the correct sequences and hygiene.
- Record observations with entities + tref/hpath links.
- Run dry consolidate + stats at natural boundaries.
- Prefer durable memory (synapsis) over cramming the context window.

This supplements (does not replace) the full content of GROK.md.