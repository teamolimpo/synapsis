"""Lightweight configuration loader for synapsis operational settings.

The canonical file is ``.synapsis/config.yaml`` (gitignored, lives with the
hot DB). It is the single place for local, instance-specific tuning of a
synapsis deployment.

Today the main consumer is the knowledge/chunk indexer
(``knowledge.include``, ``knowledge.exclude``, ``knowledge.heading_levels``,
etc.). The file is designed to grow with other sections over time.

`knowledge.include` is now **mandatory and explicit** (no more silent
classic defaults). If it is missing or empty, the chunk indexer will
fail fast with a clear actionable error ("Library paths not defined").

This module is deliberately small and forgiving for other keys:
- Bad YAML → warning + safe empty values (never crashes the MCP server or CLI).

See also:
- ``Documents/examples/synapsis-config.yaml`` (well-commented example, ships with the plugin)
- ``synapsis-extras/docs/synapsis-commands.md`` (user documentation for the knowledge section; in the TeamOlimpo extras tree)
- ``tools/knowledge_base/chunk_indexer.py`` (how the indexer consumes the config)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from tools.common.paths import resolve_relative

# ---------------------------------------------------------------------------
# Built-in defaults (kept minimal now that knowledge.include is explicit).
# ---------------------------------------------------------------------------

_DEFAULT_KNOWLEDGE: dict[str, Any] = {
    # Positive include list is NO LONGER defaulted.
    # The user (or `synapsis knowledge ...` commands) must declare it
    # explicitly in .synapsis/config.yaml. Empty include → hard error
    # at indexing time with a clear message.
    "include": [],
    # Gitignore-style exclusion patterns (applied after the include list).
    "exclude": [],
    # Heading levels to split on (1 = #, 2 = ##, ...).
    "heading_levels": [2, 3],
}

# Escalation / problem reporting levels for "solo but act as many" mode.
# Controls what happens on detected problems (task blk, hf st=fail/hold+devi,
# critical errors, explicit "workaround non-trivial", hygiene pain, etc.).
# Internal HF (handoff + task log + observe) is ALWAYS done by discipline.
# This only adds extra external escalation.
_DEFAULT_ESCALATION: dict[str, Any] = {
    # problem_reporting:
    #   "off"        - nothing extra
    #   "hf"         - only the mandatory internal handoff discipline
    #   "hf+notify"  - internal + loud notification (loud observe / banner)
    #   "hf+gh"      - internal + notify + auto-create GitHub Issue via `gh` CLI
    # Recommended for solo-in-GH-repo: "hf+gh"
    "problem_reporting": "hf+gh",
}


_config_cache: dict | None = None


def _load_raw() -> dict:
    """Read .synapsis/config.yaml if present. Returns {} on any problem."""
    path = resolve_relative(".synapsis", "config.yaml")
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            logger.warning(".synapsis/config.yaml did not contain a mapping; ignoring")
            return {}
        return data
    except Exception as exc:
        logger.warning(f"Failed to load .synapsis/config.yaml: {exc}")
        return {}


def load_config() -> dict:
    """Return the raw contents of .synapsis/config.yaml (cached)."""
    global _config_cache
    if _config_cache is None:
        _config_cache = _load_raw()
    return _config_cache


def get_knowledge_config() -> dict[str, Any]:
    """Return the 'knowledge' section (with minimal safe defaults for non-include keys).

    `knowledge.include` is intentionally NOT defaulted here anymore.
    If the user config has no (or empty) include list, callers (mainly the
    chunk indexer) are expected to fail fast with a clear, actionable error
    telling the user to configure .synapsis/config.yaml.

    This implements the "explicit config + alarm on first use" discipline.
    """
    raw = load_config().get("knowledge", {})
    if not isinstance(raw, dict):
        raw = {}

    merged = _DEFAULT_KNOWLEDGE.copy()
    merged.update(raw)

    # Normalise a little.
    # include must be an explicit list from the user config.
    # Empty list is valid at load time (the indexer will error with a clear
    # message on first use — this is the desired "alarm" behaviour).
    if not isinstance(merged.get("include"), list):
        merged["include"] = []
    if not isinstance(merged.get("exclude"), list):
        merged["exclude"] = []
    if not isinstance(merged.get("heading_levels"), list):
        merged["heading_levels"] = _DEFAULT_KNOWLEDGE["heading_levels"][:]

    return merged


def reload_config() -> None:
    """Force a reload (useful in tests or after the user edited the file)."""
    global _config_cache
    _config_cache = None


# ---------------------------------------------------------------------------
# Write / management helpers (for `synapsis knowledge` commands)
# ---------------------------------------------------------------------------


def get_config_path() -> Path:
    """Return the path to .synapsis/config.yaml (resolved relative to project root)."""
    return resolve_relative(".synapsis", "config.yaml")


def _ensure_synapsis_dir() -> Path:
    """Ensure the .synapsis/ directory exists."""
    p = resolve_relative(".synapsis")
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_config(data: dict) -> None:
    """Persist the full config dict to .synapsis/config.yaml.

    Overwrites the file. Invalidates the in-memory cache.
    """
    _ensure_synapsis_dir()
    path = get_config_path()
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            default_flow_style=False,
            sort_keys=False,
            indent=2,
            allow_unicode=True,
        )
    global _config_cache
    _config_cache = None


_STARTER_CONFIG = """# .synapsis/config.yaml
#
# Central operational configuration file for a synapsis instance.
# It lives inside the hot store (.synapsis/), is gitignored, and is meant
# to contain local, machine- or project-specific settings.
#
# This file is the single place where you configure how synapsis behaves
# at runtime. Today the main section is "knowledge" (chunk indexing).
# Over time other sections may appear (e.g. llm defaults, session behaviour, etc.).

knowledge:
  # ------------------------------------------------------------------
  # What to index ("al contrario di .gitignore")
  # ------------------------------------------------------------------
  # The "include" list is the primary positive selection.
  # These paths are relative to the project root and will be recursively
  # scanned for *.md files.
  include:
    - Library/Wiki/
    - Library/Handoff/
    # Add your own as needed:
    # - Library/notes/
    # - Library/SOPs/
    # - Library/research/
    # - Library/projects/

  # ------------------------------------------------------------------
  # What to ignore (gitignore-style patterns)
  # ------------------------------------------------------------------
  # Patterns here are applied after the include list.
  # They follow common glob rules ( ** works for recursive directories).
  # This is the recommended way to exclude drafts, archives, private stuff, etc.
  exclude:
    - Library/Archive/**
    - "**/*-draft*"
    - "**/.private/**"
    # - "Library/System/**"     # if you keep internal system notes there

  # ------------------------------------------------------------------
  # Chunking behaviour
  # ------------------------------------------------------------------
  # Which Markdown heading levels should be used as split points.
  # 1 = #, 2 = ##, 3 = ###, etc.
  # The classic (pre-config) behaviour was only levels 2 and 3.
  heading_levels: [2, 3]

  # ------------------------------------------------------------------
  # Embeddings (only relevant if you installed the optional "embeddings" extra)
  # ------------------------------------------------------------------
  # embedding_model: "sentence-transformers/all-MiniLM-L6-v2"

  # ------------------------------------------------------------------
  # Entity extraction (optional override)
  # ------------------------------------------------------------------
  # By default the indexer uses the entity_dictionary.yaml that ships
  # with the knowledge_base package. You can point to a custom one.
  # entity_dictionary: "Library/System/my-project-entities.yaml"


# ----------------------------------------------------------------------
# Future / other possible top-level sections (examples, not yet used)
# ----------------------------------------------------------------------
# llm:
#   default_provider: grok
#
# session:
#   default_token_budget: 2000
#
# indexer:
#   auto_reindex_on_handoff: true
#
# escalation:
#   problem_reporting: "hf+gh"   # off | hf | hf+notify | hf+gh

escalation:
  # ------------------------------------------------------------------
  # Problem / error escalation for solo-in-GH-repo "act as if we were many"
  # ------------------------------------------------------------------
  # Controls extra visibility when the system (or an agent) detects a real
  # problem that should not be silently worked around (task -> blk,
  # hf with st=fail/hold+devi, critical errors, explicit non-trivial
  # workaround, hygiene/consolidate pain, etc.).
  #
  # Internal HF discipline (handoff + task log + observe) is always active.
  # This setting only adds the *extra* layer.
  #
  # Values:
  #   "off"        - do nothing extra
  #   "hf"         - rely only on internal synapsis handoffs/tasks
  #   "hf+notify"  - internal + make very visible (loud observe / banner)
  #   "hf+gh"      - internal + notify + automatically create a GitHub Issue
  #                  using the `gh` CLI (must be installed + authenticated).
  #
  # For working alone in a GitHub repo we recommend starting with "hf+gh".
  # The created issue will use the synapsis-problem template (if present)
  # and will be logged back into synapsis (task event + observation).
  problem_reporting: "hf+gh"
"""


def init_knowledge_config(force: bool = False) -> Path:
    """Ensure .synapsis/config.yaml exists with a working `knowledge` section.

    Behavior:
    - If the file does not exist: write the full starter template (with comments
      and example future sections).
    - If the file exists:
        - Without --force: load the existing config and **only touch the
          `knowledge` section**. If `include` is missing or empty, append the
          common defaults (Wiki + Handoff). Ensure `heading_levels` and a
          reasonable `exclude` list exist. All other top-level sections the
          user may have added (llm:, session:, custom keys, etc.) are preserved.
        - With --force: reset only the `knowledge` keys to clean defaults
          (still preserving any other top-level sections).

    This makes `synapsis knowledge init` safe to run on an existing config
    without destroying user customizations.
    """
    path = get_config_path()

    if not path.exists():
        _ensure_synapsis_dir()
        path.write_text(_STARTER_CONFIG, encoding="utf-8")
        global _config_cache
        _config_cache = None
        logger.info(f"Created starter config at {path}")
        return path

    # File exists — we will only mutate the knowledge section (unless --force)
    cfg = _load_for_edit()

    knowledge = cfg.setdefault("knowledge", {})

    # --- include handling ---
    inc = knowledge.get("include")
    if not isinstance(inc, list):
        inc = []
        knowledge["include"] = inc

    defaults = ["Library/Wiki/", "Library/Handoff/"]

    if force:
        knowledge["include"] = defaults[:]
    else:
        # Non-force: ensure the common useful paths are present
        for d in defaults:
            if d not in inc:
                inc.append(d)

    # --- heading_levels and exclude ---
    if force or "heading_levels" not in knowledge:
        knowledge["heading_levels"] = [2, 3]

    if force or "exclude" not in knowledge:
        knowledge["exclude"] = [
            "Library/Archive/**",
            "**/*-draft*",
            "**/.private/**",
        ]

    write_config(cfg)
    action = "Reset" if force else "Updated"
    logger.info(f"{action} knowledge section in existing config at {path}")
    return path


def _load_for_edit() -> dict:
    """Load current config (or empty dict) for mutation."""
    return load_config().copy() or {}


def add_knowledge_include(rel_path: str) -> list[str]:
    """Add a path to knowledge.include (deduplicated, normalized trailing slash for dirs)."""
    cfg = _load_for_edit()
    knowledge = cfg.setdefault("knowledge", {})
    inc: list = knowledge.setdefault("include", [])
    if not isinstance(inc, list):
        inc = []
        knowledge["include"] = inc

    # Normalize: keep user's style but ensure no exact dups; add trailing / for typical Library/ dirs
    p = rel_path.strip()
    if p and not p.endswith("/") and (p.startswith("Library/") or "/" in p):
        p = p.rstrip("/") + "/"

    if p and p not in inc:
        inc.append(p)

    write_config(cfg)
    return inc


def remove_knowledge_include(rel_path: str) -> list[str]:
    """Remove a path from knowledge.include (exact match after light normalization)."""
    cfg = _load_for_edit()
    knowledge = cfg.setdefault("knowledge", {})
    inc: list = knowledge.setdefault("include", [])
    if not isinstance(inc, list):
        return []

    p = rel_path.strip().rstrip("/")
    if p and not p.endswith("/") and (p.startswith("Library/") or "/" in p):
        p = p + "/"

    new_inc = [x for x in inc if x != p and x.rstrip("/") != p.rstrip("/")]
    knowledge["include"] = new_inc
    write_config(cfg)
    return new_inc


def add_knowledge_exclude(pattern: str) -> list[str]:
    """Add a glob pattern to knowledge.exclude."""
    cfg = _load_for_edit()
    knowledge = cfg.setdefault("knowledge", {})
    exc: list = knowledge.setdefault("exclude", [])
    if not isinstance(exc, list):
        exc = []
        knowledge["exclude"] = exc

    pat = pattern.strip()
    if pat and pat not in exc:
        exc.append(pat)

    write_config(cfg)
    return exc


def remove_knowledge_exclude(pattern: str) -> list[str]:
    """Remove a glob pattern from knowledge.exclude (exact match)."""
    cfg = _load_for_edit()
    knowledge = cfg.setdefault("knowledge", {})
    exc: list = knowledge.setdefault("exclude", [])
    if not isinstance(exc, list):
        return []

    pat = pattern.strip()
    new_exc = [x for x in exc if x != pat]
    knowledge["exclude"] = new_exc
    write_config(cfg)
    return new_exc


def get_effective_knowledge_config() -> dict[str, Any]:
    """Convenience: return the knowledge section after normalization (same as get_knowledge_config)."""
    return get_knowledge_config()


# ---------------------------------------------------------------------------
# Escalation config (added for T-GH-001 error-to-issue automation)
# ---------------------------------------------------------------------------

def get_escalation_config() -> dict[str, Any]:
    """Return the 'escalation' section with safe defaults."""
    raw = load_config().get("escalation", {})
    if not isinstance(raw, dict):
        raw = {}
    merged = _DEFAULT_ESCALATION.copy()
    merged.update(raw)
    return merged


def get_problem_reporting_level() -> str:
    """Return the normalized problem_reporting level (off | hf | hf+notify | hf+gh)."""
    cfg = get_escalation_config()
    val = str(cfg.get("problem_reporting", "hf+gh")).lower().strip()
    valid = {"off", "hf", "hf+notify", "hf+gh"}
    if val in valid:
        return val
    # simple aliases
    if val in ("none", "0", "false"):
        return "off"
    if val in ("internal", "hf_only"):
        return "hf"
    if val in ("notify", "hf_notify"):
        return "hf+notify"
    if val in ("gh", "github", "issue", "hf_gh", "full"):
        return "hf+gh"
    logger.warning(f"Unknown problem_reporting '{val}', falling back to 'hf+gh'")
    return "hf+gh"
