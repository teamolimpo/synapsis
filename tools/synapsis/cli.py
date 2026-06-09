"""CLI for Synapsis — compress, vacuum, stats, migrate.

Usage::

    uv run python -m tools.synapsis compress --warm --dry-run
    uv run python -m tools.synapsis compress --cold --apply
    uv run python -m tools.synapsis vacuum
    uv run python -m tools.synapsis stats
    uv run python -m tools.synapsis migrate --dry-run
"""

from __future__ import annotations

import json
import sys

import typer
from loguru import logger

from tools.synapsis.store import SynapsisStore

# ---------------------------------------------------------------------------
# CLI App
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="synapsis",
    help="Synapsis — Unified memory layer management",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Compress command
# ---------------------------------------------------------------------------


@app.command()
def compress(
    warm: bool = typer.Option(False, "--warm", help="Compress observations (level 1)"),
    cold: bool = typer.Option(False, "--cold", help="Compress observations (level 2)"),
    task_events: bool = typer.Option(
        False, "--task-events", help="Compress task events instead of observations"
    ),
    dry_run: bool = typer.Option(True, "--dry-run", help="Show what would be done without saving"),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply compression (overrides --dry-run)",
    ),
    age_days: int | None = typer.Option(
        None, "--age-days", help="Only compress data older than N days"
    ),
) -> None:
    """Compress old observations or task events (hot/warm/cold)."""
    if not warm and not cold:
        logger.error("Devi specificare almeno --warm o --cold.")
        sys.exit(2)

    max_level = 2 if cold else 1
    do_apply = apply or not dry_run

    logger.info(
        f"Compression: max_level={max_level}, age_days={age_days}, "
        f"dry_run={not do_apply}, target={'task_events' if task_events else 'observations'}"
    )

    try:
        store = SynapsisStore()
        if task_events:
            results = store.compress_task_events(
                age_days=age_days,
                max_level=max_level,
                dry_run=not do_apply,
            )
        else:
            results = store.compress_observations(
                age_days=age_days,
                max_level=max_level,
                dry_run=not do_apply,
            )
        if isinstance(results.get("sessions_affected"), set):
            results["sessions_affected"] = sorted(results["sessions_affected"])
        if isinstance(results.get("tasks_processed"), set):
            results["tasks_processed"] = sorted(results["tasks_processed"])
        print(json.dumps(results, indent=2))
        store.close()
    except Exception as e:
        logger.error(f"Compression failed: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Vacuum command
# ---------------------------------------------------------------------------


@app.command()
def vacuum() -> None:
    """Run VACUUM and reindex the database."""
    logger.info("Running VACUUM...")
    try:
        store = SynapsisStore()
        results = store.vacuum()
        print(json.dumps(results, indent=2))
        store.close()
    except Exception as e:
        logger.error(f"VACUUM failed: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Stats command
# ---------------------------------------------------------------------------


@app.command()
def stats(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show aggregate database statistics."""
    try:
        store = SynapsisStore()
        data = store.get_stats()
        store.close()

        if json_output:
            print(json.dumps(data, indent=2))
        else:
            print("=== Synapsis Stats ===")
            s = data["sessions"]
            print(f"Sessions: {s['total']} total, {s['active']} active")
            print(f"Observations: {data['observations']}")
            print(f"Tasks: {data['tasks']['total']} total")
            for status, count in data["tasks"]["by_status"].items():
                print(f"  - {status}: {count}")
            print(f"Task Events: {data['task_events']}")
            print(f"Entities: {data['entities']}")
            print(f"Summaries: {data['summaries']}")
            te = data["token_economics"]
            print(
                f"Token Economics: discovery={te['total_discovery']}, "
                f"read={te['total_read']}, savings={te['savings_ratio'] * 100:.1f}%"
            )
    except Exception as e:
        logger.error(f"Stats failed: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Cross-ref command
# ---------------------------------------------------------------------------


@app.command(name="cross-ref")
def cross_ref(
    entity: str = typer.Argument(..., help="Entity name to search"),
    n: int = typer.Option(10, "--max-results", "-n", help="Max results per category"),
) -> None:
    """Cross-reference an entity across observations and tasks."""
    try:
        store = SynapsisStore()
        results = store.cross_reference_entity(entity, max_results=n)
        store.close()
        print(json.dumps(results, indent=2))
    except Exception as e:
        logger.error(f"Cross-ref failed: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Domain command
# ---------------------------------------------------------------------------


@app.command()
def domain(
    list_domains: bool = typer.Option(False, "--list", help="List all domains"),
    set_active: str | None = typer.Option(
        None, "--set-active", help="Set domain active (domain_id=1 or 0)"
    ),
) -> None:
    """Manage system domains."""
    try:
        store = SynapsisStore()
        if list_domains:
            domains = store.list_domains()
            for d in domains:
                status = "✓ active" if d["is_active"] else "✗ inactive"
                print(f"  {d['id']:15s}  {status}  {d['description']}")
        elif set_active:
            parts = set_active.split("=", 1)
            if len(parts) != 2:
                logger.error("Usa formato: domain_id=1 o domain_id=0")
                sys.exit(2)
            did, active_str = parts
            is_active = active_str.strip() in ("1", "true", "yes")
            if store.set_domain_active(did.strip(), is_active):
                print(f"Domain '{did.strip()}' set to {'active' if is_active else 'inactive'}")
            else:
                logger.error(f"Domain '{did.strip()}' not found")
                sys.exit(1)
        store.close()
    except Exception as e:
        logger.error(f"Domain command failed: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Hygiene command (dry consolidate + stats) — ideal for hooks and /synapsis skill
# ---------------------------------------------------------------------------


@app.command()
def hygiene(
    apply: bool = typer.Option(
        False, "--apply", help="Run real (non-dry) consolidate if triggers are met"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Run safe hygiene: dry (or real) consolidate + stats. Designed for hooks and skills."""
    try:
        # Use the server-level consolidate tool function (it handles auto vs explicit + store internally)
        from tools.synapsis.server import consolidate as synapsis_consolidate

        # The tool returns a JSON *string*
        cons_json = synapsis_consolidate(auto=True, dry=not apply)
        cons = json.loads(cons_json) if isinstance(cons_json, str) else cons_json

        store = SynapsisStore()
        stats = store.get_stats()
        store.close()

        if json_output:
            print(json.dumps({"consolidate": cons, "stats": stats}, indent=2, default=str))
        else:
            print("=== Synapsis Hygiene ===")
            print(f"Consolidate (dry={not apply}): n={cons.get('n') or cons.get('consolidated')}, "
                  f"pat={cons.get('pat') or cons.get('patterns_detected')}, "
                  f"dry={cons.get('dry') or cons.get('dry_run')}")
            s = stats["sessions"]
            print(f"Sessions: {s['active']} active / {s['total']} total")
            print(f"Observations: {stats['observations']}")
            print(f"Tasks: {stats['tasks']['total']}")
            te = stats.get("token_economics", {})
            if te:
                print(f"Token savings ratio: {te.get('savings_ratio', 0)*100:.1f}%")
    except Exception as e:
        logger.error(f"Hygiene failed: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
def main() -> None:
    """Synapsis CLI."""
    app()


if __name__ == "__main__":
    app()
