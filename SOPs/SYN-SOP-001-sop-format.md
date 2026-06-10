---
title: "SOP Format — Standard for Writing Standard Operating Procedures (synapsis adaptation)"
type: sop
doc_id: "SYN-SOP-001"
version: "v0.1"
status: draft
effective_date: "2026-06-10"
review_date: "2026-12-10"
author: "Poros"
scope: team
tags: [sops, meta, format, convention, sop-format, synapsis]
aliases: [sop-format, meta-sop, process-documentation]
supersedes: ""
---

# SOP Format — Standard for Writing Standard Operating Procedures (synapsis adaptation)

## Purpose

Define a consistent, reviewable, and verifiable format for Standard Operating Procedures in the synapsis / Grok Build environment. The format draws inspiration from strict SOP conventions (such as the OLM-SOP-001 style used in related processes) and is tailored to our runtime-integrated system (AGENTS.md, .grok/rules/, .grok/skills/, GROK.md, synapsis tasks/handoffs, and knowledge indexing). The goal is to increase consistency, auditability, and scheduled review without losing the strengths of our current loaded-rules + skills model.

## Scope

**Applies to:** All new or revised prescriptive process documentation created after the effective date that we want treated as SOPs. This includes formalizations of disciplines (git, memory/handoff, project creation, escalation, knowledge management, etc.).

**Does not apply to:**
- Agent persona/prompt files (in .grok/skills/*/persona.md or .opencode/agents/).
- Handoff files themselves (they follow the dedicated handoff skill and protocol).
- One-off notes, research handoffs, or implementation code/docs.
- The core auto-loaded critical rules in `.grok/rules/` (these may reference or be derived from SOPs but keep their special loading behavior for now).

SOPs are placed according to scope:
- Framework / shared SOPs go in `SOPs/` (at project root, git-prominent, part of the core working model).
- Private / internal / daily-work SOPs go in `Library/SOPs/` (vault-style, still indexed in knowledge).
Both are:
- Indexed by the knowledge system (alongside Wiki/ and Handoff/).
- Searchable via synapsis__search.
- Subject to the same handoff + task + observe discipline as other significant artifacts.

## Responsibilities

| Role | Responsibility |
|------|---------------|
| **Author** | Writes the SOP following this format. Ensures technical accuracy and links to implementation (skills, rules, code). |
| **Poros (or current process owner)** | Owns the meta-SOP. Reviews format compliance. Maintains compatibility notes when aligning with external processes. Triggers review cycles. |
| **Reader / Agent** | Follows the SOP when performing the described process. Logs deviations via handoff or task events. |
| **Synapsis / Knowledge system** | Indexes SOP content. Supports entity extraction for "sop" type if added to dictionary. |

## Definitions

| Term | Meaning |
|------|---------|
| **SOP** | Standard Operating Procedure — a documented, repeatable, reviewable process instruction with frontmatter, fixed sections, and explicit MUST/SHOULD/MAY rules. |
| **SYN-SOP** | SOP specific to the synapsis framework / this repository (doc_id prefix `SYN-SOP-`). |
| **OLM-SOP** | External SOP following a similar strict format (doc_id prefix `OLM-SOP-`). Mentioned for compatibility notes when relevant; not required for local use. |
| **team scope** | SOPs are stored either in `SOPs/` (framework, root-level) or `Library/SOPs/` (private), git-tracked in this repo, and indexed for the project. |
| **MUST / SHOULD / MAY** | RFC 2119 keywords. MUST = absolute (violation means the SOP is not followed). |
| **Loaded rule** | A .md file under `.grok/rules/` that is automatically included as project instruction context for agents. |
| **Skill** | A declarative procedure in `.grok/skills/*/SKILL.md` that encodes repeatable agent behavior (often the executable companion to an SOP). |

## Rules

1. Every SOP MUST have a unique `doc_id` using the convention `SYN-SOP-NNN` (or `OLM-SOP-NNN` when directly porting/adopting from an external process that uses the same format). Framework SOPs are placed in `SOPs/` at the project root; private ones in `Library/SOPs/`.
2. Every SOP MUST use the exact frontmatter fields and structure defined below.
3. Every SOP MUST have `version` (semver), `effective_date`, and `review_date` (≤12 months from effective).
4. The H1 title MUST match the frontmatter `title` exactly.
5. The required sections MUST appear in the exact order listed in "Required Sections".
6. Rules inside an SOP MUST use MUST/SHOULD/MAY (RFC 2119) and be independently verifiable.
7. Procedure steps MUST use imperative mood, active voice, and be actionable.
8. Definitions section is REQUIRED when the SOP introduces terms or references other SOPs/disciplines.
9. References section is REQUIRED.
10. Revision History is REQUIRED as the final section and must include the current version.
11. SOPs SHOULD stay concise (< ~250 lines preferred). Split complex procedures.
12. No vague language in rules or steps ("periodically", "as needed", etc.).
13. The YAML frontmatter MUST be the very first content — no leading blank lines.
14. When an SOP describes a process that has a corresponding skill or loaded rule, the SOP MUST reference it explicitly (and vice-versa in AGENTS.md / GROK.md / the skill).
15. All SOPs are subject to the synapsis mandatory workflow: create task early, produce handoff on completion/significant update, log the handoff_ref, observe.

## Procedure — How to Write an SOP (synapsis environment)

### Step 1: Decide scope and identifier
1. Determine the scope: **framework/shared** (`SYN-SOP-` placed in root `SOPs/`) or **private/internal** (placed in `Library/SOPs/`).
2. Optionally note if it aligns with an external strict SOP format (e.g. `OLM-SOP-` style).
3. Choose the next available NNN for the prefix.
4. Filename: `{doc_id}-{kebab-slug}.md` (lowercase, hyphens, English, describes the procedure).
5. Place the file in the appropriate location per scope (`SOPs/` for framework, `Library/SOPs/` for private).

### Step 2: Write frontmatter
Use this template (fields marked * are required). Place at the absolute top of the file.

```yaml
---
title: "Descriptive SOP Title"              # * Must match H1 exactly
type: sop                                   # * Always "sop"
doc_id: "SYN-SOP-XXX"                       # * (use OLM-SOP-XXX only when directly porting from an external process using that prefix)
version: "v0.1"                             # * semver
status: "draft"                             # * draft | active | review | retired
effective_date: "2026-06-10"                # * ISO date when it becomes valid
review_date: "2026-12-10"                   # * ≤ 12 months from effective
author: "Poros"                             # * Agent or human
scope: team                                 # * team (location depends on framework vs private scope — see SOP Location section)
tags: [sops, your-domain-tag]               # * Always include "sops"
aliases: [alt-name, another]                # Optional search aliases
supersedes: ""                              # doc_id of the SOP this replaces
---
```

### Step 3: Write the required sections (exact order)
1. **Title (H1)** — exact match to frontmatter title.
2. **Purpose** — 1-4 lines: why this SOP exists and when to use it.
3. **Scope** — What it covers + explicit "Does not apply to".
4. **Responsibilities** — Table with Role | Responsibility (roles, not personal names).
5. **Definitions** — Table of terms (required if new terms or cross-references).
6. **Rules** — Numbered list using MUST/SHOULD/MAY. Make them verifiable.
7. **Procedure** — Numbered steps (imperative, active voice). Use sub-steps and tables where helpful. Include code blocks for templates, commands, frontmatter examples.
8. **References** — Other SOPs, AGENTS.md, GROK.md, specific skills, handoffs (hf-), research (hf- or proteo), code paths, external standards.
9. **Revision History** — Table with at minimum: Version | Date | Author | Description of Change.

### Step 4: Apply writing discipline
- English only.
- Imperative + active voice.
- Measurable where possible.
- Use tables for options, checklists, responsibilities, definitions.
- Link to executable companions: the corresponding `/skill-name`, `.grok/rules/xx-*.md`, or synapsis tool usage.
- After writing, run the Review Checklist below.

### Step 5: Activate and maintain
1. Set `status: active` and `effective_date` once the checklist passes and it has been reviewed (via handoff or PR).
2. Create/update a task (T-SOP-XXX or related) and produce a handoff with the SOP as deliverable (include `## Wiki` contribution if it adds durable knowledge).
3. Log the handoff_ref on the task.
4. Add `SOPs/` (framework) and/or `Library/SOPs/` (private) to knowledge indexing (see `.synapsis/config.yaml` or the example in `Documents/examples/`).
5. Update AGENTS.md, GROK.md, and/or the relevant loaded rule to reference the new SOP.
6. Schedule the review_date. At review time: read the SOP, check for drift vs reality (skills, rules, actual practice), produce a handoff with findings, bump version or retire.

### Step 6: Review Checklist (before marking active)
- [ ] Frontmatter complete and valid (doc_id, version, dates, author, scope, title, tags)?
- [ ] H1 exactly matches frontmatter title?
- [ ] All 9 required sections present in order, non-empty?
- [ ] Rules use RFC 2119 MUST/SHOULD/MAY and are verifiable?
- [ ] Procedure is imperative/active voice with concrete steps?
- [ ] References section present and useful?
- [ ] Revision History present with current entry?
- [ ] English only, no vague language in normative text?
- [ ] File naming follows `{doc_id}-{slug}.md`?
- [ ] Linked to any corresponding skill / .grok/rule / AGENTS entry?
- [ ] Handoff + task log produced for the creation/update?
- [ ] Added to knowledge include (if new)?
- [ ] Review date set ≤12 months?

## Language

English only. No code-switching. Follow the same tone and rigor as established in our git discipline doc and related strict SOP conventions.

## Relationship to Existing Mechanisms

- **.grok/rules/**: Critical "always-loaded" disciplines (git workflow, synapsis memory) stay here for agent context. They SHOULD reference the corresponding SOP (in `SOPs/` for framework ones or `Library/SOPs/` for private ones) and be kept in sync. Over time we may derive loaded rules from the SOPs.
- **.grok/skills/**: The practical, slash-invokable implementation of many procedures. An SOP describes the "why + what + rules"; the SKILL.md describes the exact tool sequence and guardrails. Update both when the process changes.
- **AGENTS.md + GROK.md**: The high-level entry points and detailed manuals. They point to SOPs for the formal, reviewable versions of processes.
- **Synapsis (tasks + hf + knowledge)**: Every SOP creation or major revision is a first-class artifact: task, handoff (with optional Wiki extract), observation, and indexed content.
- **Library/Wiki/process/**: Short "decision" or "adoption" pages (like the current git-sop.md) can remain as lightweight summaries or historical records. Framework SOPs live at root `SOPs/`. Private SOPs live in `Library/SOPs/`.

## SOP Location & Indexing

We use a deliberate split (inspired by common team/vault separations for visibility vs internal use):

- **`SOPs/`** (at project root): **Framework / shared / first-class SOPs**. These define how the project works as a whole. They are git-prominent, live alongside AGENTS.md / GROK.md / .grok/, and are considered part of the public working framework. Examples: this meta-SOP, git workflow, synapsis memory discipline, project creation convention, handoff protocol, etc.
- **`Library/SOPs/`**: **Private / internal / daily-work SOPs**. These are useful for our own operations and knowledge indexing but are not promoted as core framework artifacts (more "vault" style).

Both should normally be indexed for searchability.

Add to knowledge indexing (example in `Documents/examples/synapsis-config.yaml` and the active `.synapsis/config.yaml`):
```yaml
include:
  - Library/Wiki/
  - Library/Handoff/
  - Library/projects/
  - SOPs/                  # Framework SOPs (root, part of the project model)
  - Library/SOPs/          # Private / internal SOPs
```

External alignment: When a process is defined in an external SOP using a similar strict format (e.g. OLM-SOP-XXX style), we can reference it directly or maintain a local adaptation with clear notes on the relationship.

## References

- OLM-SOP-001 style — SOP Format (inspiration for strict meta-SOP conventions)
- OLM-SOP-010 style — Project Convention (example of a concrete SOP that motivated this work)
- AGENTS.md (this repo)
- GROK.md (detailed memory & tool usage)
- .grok/rules/01-git-workflow-discipline.md (example of a very mature current discipline doc)
- .grok/rules/00-synapsis-discipline.md
- .grok/skills/handoff/SKILL.md (example of executable procedure companion)
- Library/Wiki/process/ (current lightweight process pages)
- Proteo research handoffs (e.g. hf-ca58 for git discipline research)
- .synapsis/config.yaml and Documents/examples/synapsis-config.yaml (knowledge include)
- RFC 2119

## Revision History

| Version | Date | Author | Description of Change |
|---------|------|--------|----------------------|
| v0.1 | 2026-06-10 | Poros | Initial adaptation of strict SOP format conventions (inspired by OLM-SOP-001 style) for the synapsis environment. Created on chore/sop-formalization-from-olm-sop-001 branch as part of T-SOP-002 analysis. Introduced SYN-SOP- prefix, dual location model (SOPs/ at root for framework + Library/SOPs/ for private), mapped to existing .grok/rules + skills + AGENTS/GROK model, explicit cross-links requirement, and review checklist tailored to our stack. Status draft while we test with 1-2 concrete SOPs (e.g. project creation). |
| v0.2 | 2026-06-10 | Poros | Relocated framework SOPs to root `SOPs/` per team feedback. Updated all internal references, handoff, wiki extract, and both config files. Primary home for prescriptive framework SOPs is now `SOPs/` at root. `Library/SOPs/` remains for private/internal ones. |
| v0.3 | 2026-06-10 | Poros | Verification pass on SOP-001: removed unnecessary hard references and paths to external TeamOlimpo vault (~/TeamOlimpo/Team/SOPs/), softened OLM-SOP mentions to "inspiration / compatibility" and "external strict format style". Generalized handoff reference, fixed outdated location text in Scope/Definitions/Procedure, updated team scope definition for dual model, cleaned References section. Now more self-contained for the synapsis framework while keeping light acknowledgment of origins. |
