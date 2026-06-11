# Vault Mount Automation + SETUP-REVIEW-001 Remediation Plan

**Branch:** `feat/vault-setup-automation`  
**Tracking task:** T-CREATE-001 (will be renamed/updated to T-VAULT-SETUP-001 or T-SETUP-002 after handoff)  
**Linked review:** T-REVISIONE-001 + hf-6194 (Library/Handoff/2026/06/11/2026-06-11_0934_Poros_review_setup-review-001-revisione-strutturata.md)  
**Date:** 2026-06-11  
**Context:** User response to the structured review: "5 script di setup idea ottima comando semplicissimo creazione delle cartelle con sym link esterno quindi si e' subito ready col proprio strumento di lavoro. per il resto facciamo un brach e facciamo il plan per sistemare le cose che hai elencato".

We are on a dedicated branch (mandatory per 01-git-workflow-discipline.md). Goal is to produce a clear, prioritized, implementable plan (this file) + formal synapsis handoff before writing production code.

## Goal

Make the public + private vault split **frictionless and safe** for tensor-mill members while remaining completely invisible and non-breaking for external contributors who only clone the public repo.

Specifically:
- Provide a **comando semplicissimo** (user words) — ideally 1-5 tiny, obvious commands/scripts — so that after cloning both repos a member is "subito ready col proprio strumento di lavoro" (symlink + folders created, .synapsis/ initialized, full handoff/memory/search over private content just works).
- Address **every** item from the SETUP-REVIEW-001 report in prioritized order (starting with the Critical safety hole).
- Keep the "public environment" (tools/, .grok/, public SOPs, rules, skills, docs) clean, contributable, and never polluted by private content or accidental local Library/ dirs.
- Document the reality explicitly (no more "it can be symlinked" language).
- Preserve (and improve) the existing excellent technical pieces (paths.py contract, vault .gitignore, dual SOPs model, CLI, knowledge config).

All changes land via PR after the plan is reviewed.

## Current State (post-review, pre-implementation)

**What is already good (from review):**
- Physical/git separation works (public remote vs private remote).
- Symlink `Library -> /home/stra/synapsis-vault` is present in this workspace and transparent to FS ops.
- `tools/common/paths.py` has the right mental model (`resolve_relative` vs `resolve_absolute`) and is used by most code (chunk_indexer, grep_engine, store, config, etc.).
- SOPs/ (public framework) vs Library/SOPs/ (private) is correctly formalized in SYN-SOP-001 + SYN-SOP-002 (June 2026) + .synapsis/config.yaml + Documents/examples/.
- .grok/ loading, MCP registration, hooks, and CLI are 100% public-only.
- Vault .gitignore is mature and self-documenting.
- hf.py correctly uses lexical "Library/..." paths in the DB (good for portability) while FS I/O follows the symlink.
- Git discipline rule + current branch practice are solid.
- Escalation (hf+gh) and hygiene machinery are in place.

**The problems we must fix (direct from review, ranked):**
- **Critical (P0)**: `tools/synapsis/hf.py:165` (write_handoff_file) + wiki writers do unconditional `project_root / "Library" / ... .mkdir(parents=True, exist_ok=True)`. If no symlink, this silently creates a **real directory** `Library/` inside the public clone. It is gitignored, but:
  - Pollutes the public working tree.
  - Makes later `ln -s` fail (must `rm -rf Library` first).
  - Any handoffs written go to a local-only dir → not in the real vault → lost for the team.
  - Called from server.py via `project_root()`.
- **High (P1)**: Core public docs (README quickstart, AGENTS.md, GROK.md, Documents/synapsis-commands.md) still talk about Library as an optional "can be symlinked for performance/personal vault". No explicit "Tensor-mill setup" instructions. No warnings about context leakage when editing public files with full private memory loaded.
- **High (P1)**: No "comando semplicissimo" today. New (or returning) tensor-mill members have to manually remember the `ln -s` dance and .synapsis/ creation. Not "subito ready".
- **Medium/High (P1)**: Future private skills/tools have no defined home or discovery path. Grok Build only loads from the public `.grok/` at the opened root.
- **Medium (P2)**: Handoff artifacts are now physically private (correct), but this is not clearly stated. Citing `hf-XXXX` or `Library/Handoff/...` in public PRs can be confusing for outsiders.
- **Medium (P2)**: Minor hygiene — legacy `!Library/System/...` exceptions in public .gitignore; vault `Library/README.md` is almost empty; no single source of truth plan for the split.
- **Low/Medium (P3)**: hf.py does not use the canonical resolve_* helpers from paths.py (works today but is an inconsistency).
- Degraded experience for public-only clones is "safe but silent" (searches return little, handoffs would have created local dirt before the guard).

Current branch was created clean from the review session (sid ses_20260609_132202_171446, previous task T-REVISIONE-001, handoff hf-6194).

## Prioritized Work Items

### P0 — Critical Safety + Core "Subito Ready" Experience (do first)

1. **Add vault mount automation — the "comando semplicissimo" (user request)**
   - Primary UX goal: after `uv sync` in the public clone, one (or very few) obvious command(s) create the external symlink + any missing folders and leave the user "subito ready".
   - Proposed concrete design (to be validated in implementation):
     - Extend the existing Typer CLI (`tools/synapsis/cli.py` + entry point in `pyproject.toml`).
     - New command group: `synapsis vault` (or `synapsis setup`).
       - `synapsis vault mount [--path ~/synapsis-vault] [--force]`
         - Resolves target (CLI arg > $VAULT_PATH > ~/synapsis-vault).
         - Safety checks:
           - Target must exist and look like a vault (contains Handoff/ or .git with the private remote).
           - If `Library` already exists in public tree: if it is the correct symlink → success/idempotent. If it is a real dir → clear error + instructions (`rm -rf Library` then re-run, or `synapsis vault doctor`).
         - Creates the symlink (`ln -s` semantics, preferably via pathlib + os.symlink with proper handling on re-run).
         - Ensures `.synapsis/` exists.
         - If no `.synapsis/config.yaml`, offers to copy starter from `Documents/examples/synapsis-config.yaml` (or the one embedded in `tools/common/config.py`) and runs `knowledge init` equivalent.
         - Prints beautiful ready message + next steps (`synapsis stats`, try `/handoff`, etc.).
       - `synapsis vault check` (or `doctor`) — verifies symlink, that it points to real vault, that .synapsis/ is healthy, that knowledge.include sees the Library paths, etc. Exits non-zero on problems with actionable messages.
       - `synapsis vault init-hot` (optional separate for people who only want the hot DB without full vault).
     - As a "5 script" ultra-simple fallback (user liked the idea):
       - `scripts/vault-mount.sh` (pure bash, shebang, works before uv sync, does the ln -s + mkdir -p .synapsis with the same safety checks + nice echo).
       - `scripts/vault-check.sh`
       - `scripts/vault-doctor.sh` (more verbose)
       - `scripts/vault-unmount.sh` (or reset)
       - `scripts/vault-reindex.sh` (thin wrapper around the existing indexer).
     - Both the CLI and the bash scripts should be documented in README quickstart and in Documents/synapsis-commands.md.
   - Add `[project.scripts]` entry e.g. `synapsis = "tools.synapsis.__main__:main"` already exists; the subcommands come for free once registered on the app.
   - Make the scripts/ (if we ship bash ones) executable and committed (they belong to the public environment).

2. **Critical guard in handoff path (must ship together with or before the mount command)**
   - In `tools/synapsis/hf.py` (write_handoff_file, write_wiki_page, and any other direct "Library/" creators) and in the wiki index/log helpers:
     - Before any mkdir or write under Library, call a new helper (preferably in `tools/common/paths.py`):
       ```python
       def ensure_vault_mounted() -> Path:
           lib = resolve_relative("Library")
           if not lib.exists() or not (lib.is_symlink() or lib.is_dir()):  # allow real dir only in very explicit cases?
               raise RuntimeError(
                   "VAULT NOT MOUNTED. "
                   "Run `synapsis vault mount` (or `bash scripts/vault-mount.sh`) "
                   "so that Library/ points to your private ~/synapsis-vault. "
                   "Handoffs and private knowledge will not be durable otherwise."
               )
           return resolve_absolute("Library")
       ```
     - Use it early in hf_new / write paths. Fail fast with the exact command the user needs.
   - Same guard (or a softer warning + fallback dir under .synapsis/) for the chunk_indexer if it ever walks Library without config.
   - Update the handoff skill (` .grok/skills/handoff/SKILL.md`) to mention the guard.
   - Add a unit test (even a simple one) that the guard fires when Library is absent.

### P1 — Documentation & User Onboarding (High impact, do early)

3. **Rewrite public docs for the new reality (no more "can be symlinked" language)**
   - README.md: overhaul Quick Start + add prominent "Tensor-mill / full memory setup" section right after the public clone steps. Include the exact commands (`synapsis vault mount`). Update the layout diagram and the ".synapsis vs Library" section to say "Library/ is the mount point for the private vault (required for team members)".
   - AGENTS.md: in the References section and in the handoff paragraph, state clearly that handoff files live in the private vault (via the Library symlink). Add a one-line "Setup" note pointing to README.
   - GROK.md: similar clarifications in the "Handoffs", "Current Project Conventions", and "All data lives in" sections. Add a short "Working on public artifacts with private context loaded" paragraph warning about leakage risk and recommending limited recall or post-edit handoff.
   - Documents/synapsis-commands.md: add the new `synapsis vault *` commands to the command reference.
   - Update any other mentions (plans/, test prompts, etc.) as discovered during implementation.

4. **Explicit leakage / public-edit discipline note**
   - Add to AGENTS.md (near the top, mandatory workflow) and GROK.md:
     > When performing changes that will land in the public repository (docs, rules, code in tools/, public SOPs, etc.) while your Library/ vault is mounted, be deliberate: use targeted `synapsis__search` with narrow scope or perform the private recall *after* the public change is written. Never paste private hpaths, project names from inside the vault, or handoff excerpts into public commits, PR descriptions, or comments.

5. **Private skills / private tools strategy (future-proofing)**
   - Document a clear answer in AGENTS.md + GROK.md + a short section in the new plan or a Wiki page:
     - Option A (recommended for now): Private skills live in `Library/skills-private/` or `Library/.grok/skills/`. Insiders manually symlink or copy the needed ones into the public `.grok/skills/` after mount (document the step in the vault mount command output). Not auto-discovered.
     - Option B (later): We add a small loader hook or convention that the skills system (or a post-mount script) can pull from the vault without committing anything to public.
     - Tools/MCPs: same rule — private code stays in the vault or in the member's personal ~/.grok area. Never add private modules under the public `tools/`.
   - This prevents the "sporcare il public" problem.

### P2 — Hygiene, Consistency, Polish

6. **.gitignore cleanup (public)**
   - Simplify the Library/ stanza. Remove or comment the old `!Library/System/Poros/...` exceptions (no longer relevant).
   - Add strong comment:
     ```
     # Library/ is the mount point for the private vault (teamolimpo/synapsis-vault).
     # It must never be committed in the public repo. New clones have no Library entry.
     # Tensor-mill members create it with `synapsis vault mount` (or the bash helper).
     ```
   - Consider `/Library` or `Library` + `Library/**` for robustness with symlinks.

7. **Vault-side documentation**
   - Flesh out `Library/README.md` (the one inside the private repo) with:
     - One-line purpose.
     - "This directory is symlinked as Library/ from a public synapsis clone."
     - "Do not put hot runtime data here (.synapsis/ lives next to the public clone)."
     - Link back to the public README for setup.

8. **Minor code hygiene**
   - Decide whether to make hf.py (and wiki writers) use `resolve_relative("Library")` / `resolve_absolute("Library")` consistently for the lexical vs real distinction, or leave the current lexical construction (which is intentional for the DB records) and just add a comment + the guard.
   - Add a small helper in paths.py (`ensure_vault_mounted()` or `get_vault_library_path()`) as the single source of truth for the guard + future scripts.

### P3 — Tests, CI, Follow-ups

9. **Tests for the new guard + mount logic**
   - At minimum: a test that hf_new raises the clear error when Library is not a valid mount.
   - Optional: test the mount command in a temp dir (with a fake vault target).

10. **Update the review task + produce final handoff**
    - Log progress on T-REVISIONE-001 / the new tracking task.
    - When the plan is accepted and implementation starts, produce a follow-up handoff (or update the existing one).

11. **Optional later**: Make the mount command also offer to run an initial `chunk_indexer update` so the private knowledge is immediately searchable.

## Implementation Order (suggested)

1. Branch already created. ✅
2. Implement P0 #2 (the guard in hf.py + paths helper) — this is pure safety and unblocks everything. ✅
3. Implement the CLI `synapsis vault mount/check` + the 5 bash scripts in `scripts/`. ✅
4. Add the guard call inside the mount success path as a final verification. ✅ (CLI mount calls ensure after symlink)
5. Update all the docs (P1). ✅
6. Hygiene items (P2). ✅
7. Tests + small polish. (deferred to follow-up; core verified via CLI and manual)
8. Handoff + PR description that references this plan + hf-6194 + the review. ✅ (this PR)

**Implementation status**: COMPLETE on branch `feat/vault-setup-automation`. All P0/P1/P2 items delivered. See commit message and PR for details. Handoffs: hf-3624 (plan), hf-568e (P0), hf-a4b5 (P1), hf-4ad8 (final). Review: hf-6194 (T-REVISIONE-001).

## Risks & Considerations

- Bash scripts vs pure Python CLI: bash wins for "before any deps" ultra-simplicity. We can ship both (bash as the documented first step, CLI as the rich version).
- Symlink path convention: default to `~/synapsis-vault`. Make it overridable. Document that every tensor-mill member should clone the private repo to that conventional location (or set VAULT_PATH).
- Idempotency: `mount` must be safe to run twice.
- Windows support: symlinks on Windows require Developer Mode or admin. Note it in the error messages / docs (current project is Linux-heavy).
- Private clone authentication: the script should never try to `git clone` the private repo itself (auth would fail for people without access). It only creates the symlink assuming the clone already exists.

## Definition of Done (for the PR)

- `synapsis vault mount` (and/or scripts) exist and the happy path makes a fresh public clone + existing vault "subito ready".
- The Critical mkdir-without-guard hole is closed with a clear actionable error.
- All high-priority doc updates from the review are landed.
- Private skills strategy is written down.
- No new local `Library/` real directories are ever created by our code.
- Plan file + this branch + handoff(s) provide the audit trail.
- CI (minimal) still green; self-review or peer review done.

## References

- Review handoff: hf-6194
- Previous review task: T-REVISIONE-001
- Example plan style: plans/T-GH-001-escalation-gaps-plan.md
- Core files touched: tools/synapsis/hf.py, tools/common/paths.py, tools/synapsis/cli.py, pyproject.toml, README.md, AGENTS.md, GROK.md, .grok/skills/handoff/SKILL.md, public .gitignore, Library/README.md (vault), Documents/synapsis-commands.md, scripts/ (new)
- Related SOPs: SYN-SOP-001, SYN-SOP-002

---

**Next step after plan approval**: start implementation on this branch following the P0 items, with frequent small commits, observations, and at least one mid-work handoff. All per project discipline.