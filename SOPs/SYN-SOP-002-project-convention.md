---
title: "Project Convention — Structure, Files, and Synapsis Integration"
type: sop
doc_id: "SYN-SOP-002"
version: "v0.1"
status: draft
effective_date: "2026-06-10"
review_date: "2026-12-10"
author: "Poros"
scope: team
tags: [sops, project, convention, synapsis, entity]
aliases: [project-convention, project-structure, project-entity]
supersedes: ""
---

# Project Convention — Structure, Files, and Synapsis Integration

## Purpose

Define the standard structure, required files, naming conventions, and Synapsis entity metadata for all projects under `Library/projects/`. A consistent project layout ensures that `synapsis_search("project-name")` returns structured results — status, phase, health — rather than scattered FTS5 snippets.

This SOP builds on the format and conventions established in `SYN-SOP-001`.

## Scope

**Applies to:** Every directory under `Library/projects/<name>/`. Both new and existing projects MUST conform to this convention to be recognized as valid projects by Synapsis.

**Does not apply to:** Files outside `Library/projects/` (e.g., `Library/Wiki/`, `Library/Handoff/`, `SOPs/`, `Team/`). One-off notes, scratch files, or personal documents within a project directory that do not follow the naming convention SHOULD be placed in a `log/` or `docs/` subdirectory.

## Responsibilities

| Role | Responsibility |
|------|---------------|
| **Poros** | Enforces this convention. Creates/updates Synapsis project entities via search-time fallback or re-index hook. Reviews compliance during project discovery. |
| **Author (any agent)** | Creates project files following this convention. Writes valid YAML frontmatter. Updates `STATUS.md` when project state changes. |
| **Chunk Indexer / Knowledge system** | On re-index, parses frontmatter from `README.md` and `STATUS.md`, updates Synapsis entity metadata accordingly. |

## Definitions

| Term | Meaning |
|------|---------|
| **Project** | Any directory under `Library/projects/` that contains a `README.md` with frontmatter `type: project`. |
| **SSOT** | Single Source of Truth. The filesystem IS the source of truth; Synapsis entities are derived from file content. |
| **State hash** | SHA-256 hash of `STATUS.md` content at last index. Used for change detection. |
| **Entity metadata** | JSON blob stored in Synapsis `entities.metadata` that summarizes project state for fast retrieval. |

## Rules

1. Every project MUST have a `README.md` file at its root with valid YAML frontmatter containing `type: project`.
2. Every project MUST have a `STATUS.md` file at its root with valid YAML frontmatter containing `type: project-status`.
3. A directory without `README.md` — or with a `README.md` lacking `type: project` in frontmatter — MUST NOT be treated as a project by Synapsis.
4. All filenames within a project directory MUST be in English, lowercase, with hyphens as word separators.
5. File numbering (e.g., `01-strategy.md`) MUST NOT be used. Use descriptive names without numeric prefixes.
6. The YAML frontmatter `title` field MUST match the file's H1 heading exactly.
7. The `status` field in frontmatter MUST be one of: `active`, `paused`, `completed`, `archived`.
8. The `health` field in frontmatter MUST be one of: `on_track`, `at_risk`, `off_track`, `unknown`.
9. The `phase_progress` field in `STATUS.md` MUST be a float between `0.0` and `1.0` inclusive.
10. Synapsis entity metadata MUST be derived from file frontmatter, never duplicated manually. The filesystem is the SSOT.
11. When Synapsis detects a change in `STATUS.md` (via hash comparison), it SHOULD update the entity metadata automatically.
12. If Synapsis entity metadata is missing or stale, the search function SHOULD fall back to reading `STATUS.md` directly and populate the entity lazily.

## Procedure

### Step 1: Create a new project

1. Create a directory under `Library/projects/<name>/`.
2. Write `README.md` with the required frontmatter.
3. Write `STATUS.md` with the required frontmatter.
4. Add optional files as needed: `strategy.md`, `plan.md`, `roadmap.md`, `design/`, `docs/`, `log/`.

### Step 2: Write README.md frontmatter

Use this template:

```yaml
---
title: "Project Name"
type: project
status: active              # active | paused | completed | archived
phase: "Phase 1"           # current phase label
health: on_track           # on_track | at_risk | off_track | unknown
created: 2026-06-10        # ISO date
tags: [tag1, tag2]         # search keywords
---
```

The body of `README.md` SHOULD contain:
- One-paragraph project description
- Links to relevant accounts, repos, or external resources
- Quick-reference table (optional)

### Step 3: Write STATUS.md frontmatter

```yaml
---
title: "Project Name — Status"
type: project-status
project: "projectname"       # lowercase, matches directory name
status: active
phase: "Phase 1"
phase_progress: 0.3          # 0.0 to 1.0
health: on_track
last_updated: 2026-06-10     # ISO date
---
```

The body of `STATUS.md` SHOULD contain:
- **Summary** — 2-4 lines overview of current state
- **Phases** — table or list of phases with completion status per milestone
- **Next steps** — what comes next (optional)

### Step 4: Structure the project directory

```
Library/projects/<name>/
├── README.md          REQUIRED — frontmatter type: project
├── STATUS.md          REQUIRED — frontmatter type: project-status
├── strategy.md        Optional — strategic decisions, rationale
├── plan.md            Optional — execution plan, milestones, tasks
├── roadmap.md         Optional — timeline, future evolution
├── design/            Optional — architecture, ADR, technical decisions
├── docs/              Optional — supplementary documentation
└── log/               Optional — session notes, dated decisions
```

### Step 5: Update STATUS.md on state change

When a project's phase, health, or status changes:
1. Edit `STATUS.md` — update `phase`, `phase_progress`, `health`, `last_updated`.
2. Update the body to reflect the current state.
3. The next Synapsis re-index or search-time fallback picks up the change.

### Step 6: Verify project validity

Run these checks:
- [ ] `Library/projects/<name>/README.md` exists?
- [ ] `README.md` has `type: project` in frontmatter?
- [ ] `Library/projects/<name>/STATUS.md` exists?
- [ ] `STATUS.md` has `type: project-status` in frontmatter?
- [ ] All filenames in English, lowercase, hyphen-separated?
- [ ] No numbered prefixes on filenames?
- [ ] `status` field is a valid enum value?
- [ ] `health` field is a valid enum value?
- [ ] `phase_progress` is between 0.0 and 1.0?

## Current State in This Repository (as of import)

- **OlimpoPub/**: Compliant (has proper `README.md` with `type: project` and `STATUS.md` with `type: project-status`, phases, progress, etc.).
- **pecunia/**, **tucson/**, **chimera/**: Pre-SOP / legacy (use `index.md` or non-standard frontmatter, missing required `STATUS.md` or `type: project` markers). Migration recommended as follow-up work.

## Synapsis Entity Metadata Schema

When Synapsis indexes a project, it creates or updates an entity with `entity_type='project'` and the following metadata:

```json
{
  "title": "Project Name",
  "status": "active",
  "phase": "Phase 1",
  "phase_progress": 0.3,
  "health": "on_track",
  "state_hash": "sha256:abc123...",
  "state_file": "STATUS.md",
  "files": {
    "README.md": "sha256:def456...",
    "STATUS.md": "sha256:abc123..."
  },
  "tags": ["tag1"],
  "last_updated": "2026-06-10T00:00:00"
}
```

**Populated by:** Chunk indexer (on re-index) or search-time fallback (lazy, on first miss).

**Source of truth:** File frontmatter. Entity metadata is always derived, never edited directly.

## Expected Search Behavior

When a user calls `synapsis_search("project-name", l=2)`:

```
Entity (project):
  name: project-name
  type: project
  status: active
  phase: Phase 1
  health: on_track
  state_hash: abc123...
  phase_progress: 0.3

Knowledge (chunks from project files):
  - README.md: ...
  - STATUS.md: ...
  - strategy.md: ...
```

The project entity result MUST appear before generic FTS5 chunk results.

## References

- `SYN-SOP-001` — SOP Format (`SOPs/SYN-SOP-001-sop-format.md`)
- Synapsis entities table: `tools/synapsis/store.py` (entity_type support including 'project')
- Synapsis entity_search and get_or_create_entity in `tools/synapsis/store.py`
- Previous analysis and dual-location decision: handoff hf-f67c and T-SOP-002 on branch chore/sop-formalization-from-olm-sop-001
- Current Library/projects/ contents (OlimpoPub compliant; others legacy)
- Memory Bank pattern (external reference for file-based project state)

## Revision History

| Version | Date | Author | Description of Change |
|---------|------|--------|----------------------|
| v0.1 | 2026-06-10 | Poros | Imported and adapted from OLM-SOP-010 (vault project convention). Placed as framework SOP in root `SOPs/SYN-SOP-002-project-convention.md`. Updated cross-references to SYN-SOP-001, localized to this repo's Library/projects/ state, added current compliance note for existing projects (OlimpoPub vs legacy). Follows format from SYN-SOP-001. Status draft pending migration of non-compliant projects and review. |