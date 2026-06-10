"""Minimal problem / escalation reporter for synapsis (T-GH-001).

Usage (explicit):
    from tools.synapsis.report import report_problem
    result = report_problem(
        title="Task T-FOO-123 blocked on X",
        body="...",
        tref="T-FOO-123",
        sid="ses_...",
    )
    # result["issue_url"] if created, etc.

The function always returns a dict describing what was done.
It respects the level in .synapsis/config.yaml (escalation.problem_reporting).

Internal HF discipline (handoff/task log/observe) is the caller's responsibility
or can be done here if a store is available. For the MVP we do a best-effort
task event log when tref is given.

gh CLI must be installed and authenticated for "hf+gh" level.
The reporter best-effort creates the labels declared in synapsis-problem.yml
("synapsis", "self-report") when possible. The GitHub issue is still created
even if label creation/attachment fails (the enriched body carries the context).
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from loguru import logger

from tools.common.config import get_problem_reporting_level
from tools.synapsis.store import SynapsisStore  # for optional internal logging


def _try_log_internal(
    tref: str | None,
    sid: str | None,
    title: str,
    issue_url: str | None,
    level: str,
) -> None:
    """Best-effort: log the escalation back into synapsis (task event + observe)."""
    if not tref and not sid:
        return
    try:
        store = SynapsisStore()
        if tref:
            details = f"[escalation] level={level} title={title[:80]}"
            if issue_url:
                details += f" gh={issue_url}"
            store.add_task_event(
                task_id=tref,
                event_type="note",
                details=details,
                handoff_path=None,
            )
        if sid:
            # loud session observe for hf+notify / escalations (P2 #7)
            store.add_observation(
                session_id=sid,
                type="system",
                content=f"[ESCALATION {level}] {title} -> {issue_url or 'internal only'}",
                agent="Poros",
                entities=["escalation", tref or "N/A"],
                handoff_path=None,
                task_ref=tref,
            )
        store.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Internal escalation log failed (non-fatal): {exc}")


def _ensure_label(label: str) -> bool:
    """Best-effort: create a GitHub label if it does not exist.

    Used for the labels declared in .github/ISSUE_TEMPLATE/synapsis-problem.yml
    ("synapsis", "self-report"). Returns True if the label is now usable
    (created or already existed). All errors are swallowed so that we can still
    create the issue even without label write permission.
    """
    cmd = [
        "gh",
        "label",
        "create",
        label,
        "--description",
        "Created automatically by synapsis escalation reporter (T-GH-001)",
        "--color",
        "6D28D9",  # indigo / purple
    ]
    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        logger.info(f"Created missing label '{label}' for escalation reports")
        return True
    except subprocess.CalledProcessError as exc:
        raw = exc.stderr or b"" if isinstance(exc.stderr, (bytes, bytearray)) else (exc.stderr or "")
        stderr = raw.decode(errors="ignore").lower() if isinstance(raw, (bytes, bytearray)) else str(raw).lower()
        if "already exists" in stderr:
            return True
        if "authentication" in stderr or "not logged in" in stderr or "gh auth login" in stderr:
            logger.warning(f"gh CLI not authenticated for label creation. Run `gh auth login` (escalation will proceed without custom label).")
        elif "rate limit" in stderr or "too many requests" in stderr:
            logger.warning(f"GitHub rate limit while creating label '{label}'. Proceeding without it.")
        else:
            logger.warning(f"Could not create label '{label}' (proceeding without it): {str(exc.stderr or '')[:200]}")
        return False
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Label creation for '{label}' skipped: {exc}")
        return False


def _create_github_issue(title: str, body: str, labels: list[str]) -> str | None:
    """Call gh issue create.

    Best-effort ensures the desired labels exist first (so hf+gh "just works"
    even if the labels have never been created in the repo).

    If attaching labels still fails, falls back to creating the issue without
    --label so that the self-report is not lost. Returns the URL or None.
    """
    # Best-effort: provision the labels we care about (synapsis, self-report)
    usable_labels: list[str] = []
    for lab in labels:
        if _ensure_label(lab):
            usable_labels.append(lab)

    def _run_create(use_labels: list[str]) -> str | None:
        cmd = [
            "gh",
            "issue",
            "create",
            "--title",
            title,
            "--body",
            body,
        ]
        if use_labels:
            cmd += ["--label", ",".join(use_labels)]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            url = result.stdout.strip()
            logger.info(f"Created GH issue: {url}")
            return url
        except FileNotFoundError:
            logger.warning("`gh` CLI not found in PATH – cannot create GitHub issue. Install gh and run `gh auth login` for hf+gh escalations.")
            return None
        except subprocess.CalledProcessError as exc:
            raw = exc.stderr or b"" if isinstance(exc.stderr, (bytes, bytearray)) else (exc.stderr or "")
            stderr = raw.decode(errors="ignore").lower() if isinstance(raw, (bytes, bytearray)) else str(raw).lower()
            if "authentication" in stderr or "not logged in" in stderr or "gh auth login" in stderr:
                logger.warning("gh CLI authentication failed. Run `gh auth login` to enable real GitHub issue creation for escalations (level=hf+gh).")
            elif "rate limit" in stderr or "too many requests" in stderr:
                logger.warning("GitHub rate limit exceeded while creating escalation issue. Consider using level=hf+notify for now or wait.")
            else:
                logger.warning(f"`gh issue create` failed: {str(exc.stderr or exc)[:200]}")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("`gh issue create` timed out")
            return None

    # First attempt (with any labels we successfully ensured)
    url = _run_create(usable_labels)
    if url:
        return url

    # Fallback: create the issue even without our custom labels
    # (the enriched body still carries full context + tref/sid)
    if usable_labels:
        logger.warning("Retrying GH issue creation without custom labels (body still contains full report)")
        url = _run_create([])
        if url:
            return url

    return None


def _get_git_sha() -> str:
    """Best-effort short git SHA for Context in escalations."""
    try:
        import subprocess

        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "N/A"


def report_problem(
    title: str,
    body: str,
    *,
    tref: str | None = None,
    sid: str | None = None,
    level: str | None = None,
    labels: list[str] | None = None,
    # Structured workpad fields (P2 #6) - align with escalation-policy.md + synapsis-problem.yml
    context: str | None = None,
    error: str | None = None,
    workaround: str | None = None,
    analysis: str | None = None,
    strict: bool = False,  # P2 #9: if True and gh creation fails for hf+gh, surface error
) -> dict[str, Any]:
    """Report a problem according to the configured escalation level.

    Always attempts to log back into synapsis when tref is provided.
    Creates a GitHub Issue (with the synapsis-problem template if present)
    only when the effective level is "hf+gh".

    Structured fields (context, error, workaround, analysis) are preferred
    for better workpad alignment with the policy and issue template.
    If omitted, falls back to using the flat `body` under Error/Deviation/Block.

    strict=True: if hf+gh and issue creation fails, result will contain "error".
    """
    effective = level or get_problem_reporting_level()

    # P2 #9: support additional custom labels while always including core ones
    core_labels = ["synapsis", "self-report"]
    provided = labels or []
    all_labels = list(dict.fromkeys(core_labels + provided))  # dedup, preserve order

    result: dict[str, Any] = {
        "title": title,
        "effective_level": effective,
        "internal_logged": False,
        "notified": False,
        "issue_url": None,
        "error": None,
    }

    # 1. Internal logging (best effort)
    _try_log_internal(tref, sid, title, None, effective)
    result["internal_logged"] = True

    # 2. Notify (loud in logs / future session context)
    if effective in ("hf+notify", "hf+gh"):
        logger.warning(f"[ESCALATION {effective}] {title}")
        result["notified"] = True

    # 3. GitHub Issue
    if effective == "hf+gh":
        # P2 #9: basic duplicate prevention for same tref (recent escalation already logged with gh URL)
        if tref:
            try:
                store = SynapsisStore()
                recent = store.get_task_events(tref, limit=5)
                for e in recent or []:
                    det = str(e.get("details") or "")
                    if "[escalation]" in det.lower() and "gh=" in det.lower():
                        logger.info(f"Skipping duplicate hf+gh escalation for tref={tref} (recent issue already logged)")
                        store.close()
                        return result
                store.close()
            except Exception:
                pass

        git_sha = _get_git_sha()
        # Build workpad-style body matching policy + template sections
        main_error = error or body or "(see title and details)"
        ctx = context or ""
        if tref or sid or git_sha:
            ctx = (ctx + "\n" if ctx else "") + "\n".join(
                f"- {k}: {v}"
                for k, v in [
                    ("tref", tref or "N/A"),
                    ("sid", sid or "N/A"),
                    ("git", git_sha),
                ]
                if v
            )

        enriched = (
            f"**Synapsis self-detected problem** (level={effective})\n\n"
            f"**Context**\n{ctx.strip() or '(none provided)'}\n\n"
            f"**Error / Deviation / Block**\n{main_error.strip()}\n\n"
            f"**Attempted workaround**\n{(workaround or '(none provided)').strip()}\n\n"
            f"**What needs to be analyzed / next action**\n{(analysis or '(to be determined)').strip()}\n\n"
            "---\n"
            "*Created by synapsis reporter – see escalation-policy.md*"
        )
        url = _create_github_issue(title, enriched, all_labels)
        if url:
            result["issue_url"] = url
            # log the URL back
            _try_log_internal(tref, sid, title, url, effective)
        else:
            result["error"] = "Failed to create GitHub issue"
            if strict:
                logger.error(f"Strict mode: hf+gh escalation failed to create issue for title={title[:60]}")

    return result
