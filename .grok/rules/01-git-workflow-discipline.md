# Git Workflow Discipline — Project Rules (loaded via .grok/rules/)

**Core rule (mandatory)**: Before *any* modification — code, docs, config, tests, even trivial one-liners or typos — create a dedicated branch. **Never work directly on main**. All changes must land on main via a Pull Request (PR) after passing CI gates and appropriate review. This is non-negotiable and is the direct companion discipline to our synapsis memory/handoff rules (see AGENTS.md and GROK.md).

This rule formalizes the explicit lesson from hf-8565 (T-GH-001 reflection): direct commits/pushes to main + retroactive PR attempts (which failed because head==base) are anti-patterns. "Così non si lavora in team" (or as a solo developer acting professionally / "act as if we were many").

## Why this exists
- Strong synapsis discipline (tasks, observations, handoffs, escalation via T-GH-001) is insufficient without matching git/PR process discipline.
- Bypassing the flow creates: bypassed review/CI, hard reverts, polluted history, risk of broken main, technical debt, and "oops" moments that require later cleanup.
- Even "simple" or "banale" changes follow the same rules — this is what structured teams and disciplined independents do.

## Mandatory Flow (team members and solo "act as if we were many")

1. Create a short-lived topic branch: `git checkout -b feat/<slug>`, `fix/<slug>`, `chore/<slug>`, `docs/<slug>` (use conventional prefixes; keep scope small).
2. Make focused changes + meaningful commits on the branch only.
3. Push the branch (`git push -u origin ...`).
4. **Open the PR before any merge intent**. Link related work (T-XXX tasks, hf-YYYY handoffs, issues). Describe context, risks, testing.
5. Ensure all required CI / status checks are green.
6. Review:
   - For shared work: at least one approving review (code owners or peers where applicable).
   - For solo rigor: use draft PR + explicit self-review comment, or local validation before marking ready. The PR artifact provides the audit trail.
7. Merge (squash or rebase preferred for clean linear main history, consistent with common protection rules).
8. Delete the branch after merge.

**PRs are opened on the branch, not after landing on main.**

## Branch Protection (target repo settings)
We use a **Repository Ruleset** named `main-branch-discipline` (Settings → Rules → Rulesets) targeting `~DEFAULT_BRANCH`. This is more modern and flexible than classic branch protection rules.

Current active rules in the `main-branch-discipline` ruleset (targeting `~DEFAULT_BRANCH`, as of 2026-06-10):

- Require a pull request before merging
  - Required approving reviews: 0 (solo "act as team" mode)
  - Dismiss stale pull request approvals when new commits are pushed: enabled
  - Require approval of the most recent reviewable push: **disabled** (for now)
    - Reason: when you are the only person with write access, enabling this creates an unresolvable block ("someone other than the last pusher must approve"). We keep it off while solo. It can (and should) be turned back on as soon as there is at least one other collaborator with write access.
- Require linear history: enabled
- Block force pushes: enabled
- Block branch deletions: enabled

(Note: the "Require status checks to pass before merging: `ci` (strict)" rule that was added during T-CI-001 was manually removed from the live ruleset during merge of long-lived escalation work — see T-CI-002 below. The minimal `ci.yml` workflow remains in the repo and can still be used as a signal.)

Bypass: never (the ruleset cannot be bypassed).

**T-CI-001 (high priority)**: Completed. Minimal CI workflow added. "Require status checks" (with strict) was temporarily enabled in the ruleset but later relaxed for the reasons documented in T-CI-002.

Note on solo configuration: the "Require approval of the most recent reviewable push" setting is intentionally left **disabled** while there is only one person with write access (see explanation above). The other rules (PR required, linear history, status checks strict, no force push, no direct push to main) already provide strong mechanical enforcement.

If the ruleset cannot be made fully strict immediately, the documented cultural rule + mandatory handoff call-outs for any deviation still apply. Use the T-GH-001 escalation mechanisms (see issue #3) for process "devi" or "blk".

## Scope
- Applies to **every** change. No exceptions for "quick", "trivial", "docs only", or "I know what I'm doing".
- Recovery: prefer `git revert` (via PR where possible) over reset/force-push on shared history. Protection makes dangerous operations harder.
- Ties to broader process: deviations (as documented in hf-8565) must be called out honestly in handoffs, tasks, and observations. This enables the "act as if we were many" via visibility (see escalation policy).

## Evidence & References (from proteo research hf-ca58)
- **Branching discipline (CONFIRMED)**: GitHub Flow (official docs, HIGH): dedicated branch for *any* change including docs/policy/typos. "Create a branch ... without affecting the default branch" + review opportunity. trunkbaseddevelopment.com (MED-HIGH): short-lived branches standard; direct-to-trunk only for tiniest cases in very small disciplined teams. Multiple handbooks/tutorials (MED): "Always create a feature branch, even for small fixes"; "Never push directly to main".
- **PR before merge + protection (CONFIRMED core; PARTIALLY CONFIRMED on ultra-trivial variance)**: GitHub Flow requires PR for feedback; protected branches enforce "only via a pull request". Configurable approvals + last-push rules. SIFT performed in hf-ca58: strong FOR from GitHub's own usage, TBD, team rules, internal reflection; some AGAINST on friction for one-liners in forums (context-dependent).
- **CI as gate (CONFIRMED)**: Required status checks in protection rules. "Nothing lands on main without green."
- **Anti-patterns & costs (CONFIRMED)**: Direct main bypasses everything; post-facto PRs fail or are empty; long-lived branches; no protection. hf-8565 (project internal, HIGH): exact deviation during T-GH-001 work despite "discussing good practices"; user correctly called it out as non-team behavior; "Flusso corretto ribadito: branch -> modifiche -> test -> PR -> merge". External: broken main risk, hard reverts, debt (protection rationale + DevOps literature).
- **Adaptations (incl. solo)**: Large: heavier gates (GitFlow or strict Flow + owners/queues). Small (2-6): GitHub Flow/TBD + basic protection (1 approval + checks). Solo "act as if we were many" (project context from T-GH-001/escalation): adopt identical (branch even trivial, PR/draft/self-review artifact, local CI simulation) to get the gates and history benefits. Matches this project's "solo-in-GH-repo" reality.
- **Model comparison (CONFIRMED)**: GitHub Flow (lightweight, short branches, mandatory PR to main, main always deployable). GitFlow (more ceremony, long-lived branches for releases). Trunk-based (short branches + PRs preferred; direct only with extreme discipline + automation). All professional variants isolate changes + add gates over direct main for shared/maintained work.

Full map, source assessments (Tier 1 per source + Tier 2 per finding), SIFT details, and gaps (e.g. specific public post-mortems on direct-main incident costs were UNVERIFIABLE in the research searches) live in:
- Handoff: hf-ca58 (Library/Handoff/2026/06/10/2026-06-10_1021_proteo_analysis_githubgit-sop-best-practices-landscape.md)
- Extracted Wiki: Library/Wiki/github/team-workflow.md (kind: research)

## Enforcement & Evolution
- This rule is loaded automatically (any *.md under .grok/rules/).
- Reference it in handoffs, task logs, and observations for any git-related work.
- Update AGENTS.md + this file + GROK.md together when patterns evolve (per AGENTS guidance).
- For significant process decisions or deviations, produce a formal handoff (`/handoff` skill or direct `synapsis__hf`) + task log (evt "hr").
- **T-CI-001** (2026-06-10): Minimal CI workflow added.

**T-CI-002** (2026-06-10): During landing of major escalation work on a long-lived feature branch (`test/verify-escalation-t-gh-001`, PRs #39/#40), the 'ci' required status check (strict) blocked the merge despite the workflow file being present in the tree and the job having run. The check showed "Expected — Waiting for status to be reported" / empty statusCheckRollup on the head SHA (classic head-SHA + strict + PR-only trigger + minimal placeholder friction, confirmed by proteo SIFT in hf-9cbf and mechanics in hf-334e). The rule was manually removed from the live ruleset to unblock the merge. 

This was a process deviation ("devi") from the policy as documented at the time ("Bypass: never"). Research (hf-af55) surfaced it as a known practitioner pattern for exactly these conditions. Current live ruleset has no required_status_checks for 'ci'. The minimal workflow stays (useful signal even if not mechanically required).

Doc updated for honesty (no more drift between loaded rules and reality). Any future re-enable or relax must follow the hygiene in "Enforcement & Evolution" + explicit handoff + task log.

Future enhancements (optional): pre-commit/push hooks, CI checks that fail on direct main patterns, or branch naming conventions.

**SOP is our guide.** We do not "discuss best practices while pushing to main." We follow the flow, call out deviations explicitly when they happen, and improve the process visibly.

Refs: hf-ca58 (proteo research), hf-8565 (T-GH-001 cautionary example + Wiki lesson), T-ANALISI-001, T-SOP-001, GitHub Flow + Protected branches docs, trunkbaseddevelopment.com, Atlassian workflow comparisons, project AGENTS.md / GROK.md / escalation mechanisms.

This supplements (does not replace) the full content of AGENTS.md and GROK.md. Follow the synapsis mandatory workflow in all non-trivial work.