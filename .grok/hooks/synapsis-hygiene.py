#!/usr/bin/env python3
"""Lightweight synapsis hygiene runner for Grok hooks.

Intended to be called from .grok/hooks/*.json on Stop / PreCompact / SessionEnd.
Safe, read-mostly operations (dry consolidate, stats). Never mutates without explicit flags.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure we can import the local tools package when hook runs from any CWD
ROOT = Path(__file__).resolve().parents[2]  # .grok/hooks/ -> project root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GROK_HOOK_EVENT", "unknown")

    try:
        from tools.synapsis.store import SynapsisStore
    except Exception as e:
        print(f"[synapsis-hygiene] cannot import store: {e}", file=sys.stderr)
        sys.exit(0)  # fail-open for hooks

    try:
        store = SynapsisStore()
    except Exception as e:
        print(f"[synapsis-hygiene] cannot open store: {e}", file=sys.stderr)
        sys.exit(0)

    try:
        if event in ("pre_compact", "PreCompact"):
            res = store.consolidate(auto=True, dry_run=True)
            compact = {
                "event": event,
                "consolidated": res.get("consolidated"),
                "patterns": res.get("patterns_detected"),
                "contradictions": res.get("contradictions_detected"),
                "dry": res.get("dry_run"),
            }
            print("[synapsis-hygiene]", json.dumps(compact, default=str)[:900])

        # Always cheap stats on Stop / end events
        if event in ("stop", "Stop", "session_end", "SessionEnd", "pre_compact", "PreCompact"):
            stats = store.get_stats()
            te = stats.get("token_economics", {})
            print(
                f"[synapsis-hygiene {event}] sessions={stats['sessions']['active']}/{stats['sessions']['total']} "
                f"obs={stats['observations']} tasks={stats['tasks']['total']} "
                f"savings~{te.get('savings_ratio', 0)*100:.0f}%"
            )
    except Exception as e:
        print(f"[synapsis-hygiene] error during {event}: {e}", file=sys.stderr)
    finally:
        try:
            store.close()
        except Exception:
            pass

if __name__ == "__main__":
    import os
    main()
