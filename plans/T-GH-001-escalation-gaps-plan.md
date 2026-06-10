# T-GH-001 Escalation / Self-Reporting Mechanism — Gaps & Improvement Plan

**Branch:** `test/verify-escalation-t-gh-001`  
**Tracking task:** T-CLI-002  
**Date of discovery:** 2026-06-10  
**Context:** Follow-up to landing the initial T-GH-001 feature ("feat(escalation)"). Discovery performed after the user requested a "bella discovery su quello che abbiamo fatto e su cosa funziona".

## Goal
Make the mechanism reliable and complete so that when Grok (or any agent) encounters real difficulties (`blk`, handoff with `devi` or bad `st`, non-trivial workarounds, critical errors, hygiene pain), it **cannot** silently work around them. Problems must be externalized, preferably as structured GitHub Issues when `hf+gh` is configured (the recommended setting for solo-in-GH-repo "act as if we were many").

This plan is based on:
- Code review of `report.py`, `cli.py`, `server.py`, `hf.py`, `tools/common/config.py`
- Live execution tests (safe levels `hf` and `hf+notify`)
- Policy document (`.synapsis/escalation-policy.md`)
- Issue template
- References in AGENTS.md / GROK.md

## Current State (Summary from Discovery)
- **Effective level**: `hf+gh` (via code defaults).
- **Critical hygiene issue**: The project's actual `.synapsis/config.yaml` **does not contain an `escalation:` section at all**. It only has the `knowledge` section. The mechanism therefore depends entirely on hard-coded defaults in `tools/common/config.py`.
- **Core reporter** (`report_problem`): Solid, best-effort, defensive. Internal logging always works. Notify and GH paths are implemented.
- **CLI**: Functional (`synapsis problem`).
- **Auto trigger**: Only on task `blk` in the MCP server (very minimal — "semplice semplice").
- **gh CLI**: Available in the environment.
- **Docs/Policy/Template**: Present and reasonably good.
- **Major gaps**: See below.
- **Test coverage**: Zero for this feature.

## Prioritized Gaps & Work Items

### P0 — High Priority (Core Contract Violations)
1. **Handoff Integration (Biggest missing piece)**
   - `hf.py` fully supports `devi` (in frontmatter + `hf_new`) and `st` values like `fail` / `hold` / `kill`.
   - `.synapsis/escalation-policy.md` explicitly requires escalation on handoffs with bad `st` or non-empty `devi`.
   - **Reality**: No call to `report_problem` anywhere in the handoff creation path (`hf_new`, `hf` MCP tool, etc.).
   - **Action**: Wire escalation inside or right after `hf_new` when conditions are met. Pass `tref`, `sid` (if available), and a good body derived from the handoff.
   - **Considerations**: Should it be inside `hf_new` (after the file is written) or in the MCP server layer? Avoid double-escalation.

2. **Strengthen & Expand Automatic Triggers**
   - Current blk handler (server.py:916) is minimal: no `sid`, basic body, only on status change.
   - Policy lists more triggers: critical runtime errors, hygiene/consolidate pain, explicit non-trivial workaround decisions.
   - **Actions**:
     - Improve blk handler (pass `sid` when available from the MCP call).
     - Add hooks/calls from other relevant places (e.g. consolidate results, error paths in server/hf/store).
     - Consider an explicit API for agents to say "this is a non-trivial workaround, escalate".

### P1 — High Priority (Quality & Confidence)
3. **Explicit escalation section in the project's `.synapsis/config.yaml`**
   - This is the concrete instance of the mechanism not being "configured" in the running project (only defaulted in code).
   - The file currently contains only the `knowledge:` section.
   - This is a hygiene and "act as if we were many" issue: the config should explicitly declare the desired reporting level with comments.
   - **Actions**:
     - Add a proper `escalation:` section to `.synapsis/config.yaml` (copy/adapt the commented example from the starter in `tools/common/config.py`).
     - Document why we set it to `hf+gh` (or whatever we decide).
     - Consider whether `synapsis knowledge init` (or a new command) should ensure this section exists going forward.

4. **Add Test Coverage**
   - Currently zero tests touch `report_problem`, the problem CLI, auto-escalation, or config resolution for escalation.
   - **Actions**:
     - Unit tests for `report_problem` (all 4 levels, internal logging, notify, mock gh for hf+gh).
     - CLI tests for `synapsis problem`.
     - Integration-style test for the blk auto path (via the MCP server task update).
     - Test config fallbacks and aliases in `get_problem_reporting_level`.
   - Add to existing `tools/synapsis/test_*.py` or new `test_report.py`.

5. **Validate the hf+gh Path End-to-End (Safely)** — **DONE on branch test/verify-escalation-t-gh-001 (T-CLI-002)**
   - Controlled real escalation performed 2026-06-10 via `report_problem(..., level="hf+gh", tref="T-CLI-002")`.
   - Result: https://github.com/teamolimpo/synapsis/issues/11 created successfully.
   - Labels: `synapsis` + `self-report` (auto-provisioned by `_ensure_label`, correct description "Created automatically by synapsis escalation reporter (T-GH-001)" and color 6D28D9).
   - Body: exactly matches `report.py` enrichment:
     - `**Synapsis self-detected problem** (level=hf+gh)`
     - `- tref: T-CLI-002`
     - `- sid: N/A`
     - User body + `---` + `*Created by synapsis reporter – see escalation-policy.md*`
   - Note on template: the `synapsis-problem.yml` form template was **not** attached (expected — automation uses manual `--body` enrichment; the yml is intended for manual/web creation via GitHub UI).
   - Back-log: confirmed in code path (post-gh `_try_log_internal` added note event #255 on T-CLI-002 containing the gh URL). Internal log + notify also happened.
   - Auth/permission: none — `gh` CLI was authenticated and had sufficient rights to create labels + issue.
   - Test issue #11 can/should be closed after review.
   - (See task T-CLI-002 logs and gh issue view for full artifact.)

### P2 — Medium Priority (Hygiene & Completeness)
6. **Improve Issue Body / Workpad Quality** — **IN PROGRESS (core done)**
   - `report_problem` now accepts optional structured params: `context`, `error`, `workaround`, `analysis`.
   - Auto-enriches with tref/sid + git short sha.
   - Produces proper sections matching policy + template (Context, Error/Deviation/Block, Attempted workaround, What needs to be analyzed).
   - Updated call sites (blk, hf devi, consolidate hooks) to pass `error=` and `analysis=` where meaningful.
   - Legacy `body=` still works (goes into Error section).
   - Git sha helper added (best-effort).
   - Tests still pass; demo call with structured fields succeeds.
   - (Further polish: more auto-context from recent hf, better defaults in call sites can be follow-up.)

7. **sid Propagation & Observability** — **IN PROGRESS (core done)**
   - Added `sid` param to `hf_new` and to the `hf()` MCP tool; threaded through to the escalation call in handoff path.
   - Enhanced `_try_log_internal`: now logs loud session observe (type=system, entities) if sid present, even without tref (for sid-only cases). Task event still requires tref.
   - Blk and consolidate paths already propagate sid (from prior points).
   - hf+notify will now produce the system observe when sid provided by caller.
   - Main propagation gap for handoffs closed. Further threading (e.g. in more internal paths) can follow.

### P3 — Lower Priority / Polish
8. **Documentation & Examples**
   - Add concrete usage examples (both explicit `report_problem` calls from agent code and the auto paths).
   - Document the current state of automatic triggers vs. explicit escalation.
   - Update GROK.md / AGENTS.md with "how to escalate from your agent" guidance if missing.
   - Possibly add a small example in `Documents/examples/`.

9. **Robustness & Edge Cases**
   - Better messages when `gh` is not authenticated or rate-limited.
   - Duplicate issue prevention heuristics (e.g. recent similar issues for the same tref)?
   - Support for additional custom labels via the call.
   - Consider whether best-effort should ever have a "strict" mode that surfaces failures to the caller.

10. **Broader Integration Points**
    - Wire escalation from other core paths (knowledge indexing failures, consolidate pain, store errors, etc.).
    - Ensure sub-agents / spawned agents (via spawn_subagent etc.) can easily escalate.
    - Link escalation issues back to tasks/handoffs more visibly (already partially done via events).

## Suggested Phasing (for work on this or follow-up branches)
- **Phase 1 (Core contract)**: Handoff wiring (#1) + improve blk trigger (#2) + explicit project config section (#3) + basic tests (#4).
- **Phase 2 (Confidence)**: End-to-end hf+gh validation (#5).
- **Phase 3 (Polish)**: Body quality (#6), sid/observability (#7), docs (#8), robustness (#9).

## Success Criteria
- When a handoff is created with `devi` or `st=fail/hold`, an escalation is triggered (at the configured level).
- blk transitions reliably create (or attempt) escalation with good context.
- All main paths have test coverage.
- A real `hf+gh` escalation has been performed and the resulting issue + back-link verified.
- The project's `.synapsis/config.yaml` explicitly contains (and documents) the `escalation:` section with the chosen level.
- Agents have a clear, documented way to escalate difficulties.

## References
- `.synapsis/escalation-policy.md`
- `tools/synapsis/report.py`
- `tools/synapsis/cli.py`
- `tools/synapsis/server.py` (task update blk handler)
- `tools/synapsis/hf.py` (handoff creation)
- `tools/common/config.py` (get_problem_reporting_level and defaults)
- `.github/ISSUE_TEMPLATE/synapsis-problem.yml`
- AGENTS.md section on Escalation (T-GH-001)
- GROK.md escalation notes
- Original landing commit: "feat(escalation): land complete T-GH-001 self-reporting mechanism (fixes #1 + hf+gh robustness)"

## Next Steps After This Plan
- Review this plan with the user.
- Decide scope for the current branch (or spawn follow-up branches/PRs).
- Use synapsis task updates + handoff when significant pieces are completed.
- Before any code change: we are already on the correct dedicated branch.

---
*This plan was generated during structured discovery on `test/verify-escalation-t-gh-001` following project git + synapsis discipline.*
