"""Grep engine for Knowledge Base search.

Handles ripgrep (preferred) and grep (fallback) execution,
output parsing, snippet construction, and frontmatter extraction.

Usage
-----
    from tools.knowledge_base.grep_engine import search

    results_json = search("MCP", project_root, scope="wiki")
"""

from __future__ import annotations

import datetime  # noqa: TC003 — used for YAML date type check
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from tools.common.paths import resolve_relative

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCOPE_PATHS: dict[str, list[str]] = {
    "wiki": ["Library/Wiki/"],
    "docs": ["Library/Wiki/"],  # adjust to whatever you actually put in knowledge.include
    "wiki+docs": ["Library/Wiki/"],
    "all": ["Library/", "Team/Handoff/"],
    "handoff": ["Team/Handoff/"],
}

VALID_SCOPES = frozenset(SCOPE_PATHS)

_RE_FM_DELIMITER = re.compile(r"^---\s*$", re.MULTILINE)

DEFAULT_TIMEOUT = 10
DEFAULT_MAX_RESULTS = 15
DEFAULT_CONTEXT_LINES = 3
MAX_RESULTS_LIMIT = 50
MAX_CONTEXT_LINES = 10
MAX_QUERY_LENGTH = 500

# ---------------------------------------------------------------------------
# Engine detection
# ---------------------------------------------------------------------------


def get_grep_engine() -> tuple[str, str]:
    """Return ``(engine_name, engine_path)``.

    Prefers ripgrep (``rg``) over ``grep``. Raises ``RuntimeError`` if
    neither is found on ``PATH``.
    """
    rg = shutil.which("rg")
    if rg:
        return ("ripgrep", rg)
    grep = shutil.which("grep")
    if grep:
        return ("grep", grep)
    raise RuntimeError("Neither ripgrep (rg) nor grep found on PATH.")


# ---------------------------------------------------------------------------
# Frontmatter extraction (per file, cached)
# ---------------------------------------------------------------------------

_frontmatter_cache: dict[str, dict[str, Any]] = {}
_fm_range_cache: dict[str, tuple[int, int] | None] = {}
_file_lines_cache: dict[str, list[str]] = {}


def clear_caches() -> None:
    """Clear all internal caches (for testing)."""
    _frontmatter_cache.clear()
    _fm_range_cache.clear()
    _file_lines_cache.clear()


def _get_file_path_key(file_path: Path, project_root: Path) -> str:
    """Return a canonical key for a file path."""
    try:
        return str(file_path.relative_to(project_root))
    except ValueError:
        return str(file_path.resolve())


def get_frontmatter(file_path: Path, project_root: Path) -> dict[str, Any]:
    """Extract YAML frontmatter from a Markdown file.

    Returns a dict with keys ``title``, ``date``, ``tags`` (when present).
    Results are cached per file.
    """
    key = _get_file_path_key(file_path, project_root)
    if key in _frontmatter_cache:
        return _frontmatter_cache[key]

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        _frontmatter_cache[key] = {}
        return {}

    parts = _RE_FM_DELIMITER.split(content)
    if len(parts) >= 3:
        try:
            fm = yaml.safe_load(parts[1])
            if isinstance(fm, dict):
                filtered: dict[str, Any] = {}
                for k in ("title", "date", "tags"):
                    if k in fm:
                        val = fm[k]
                        # Convert non-serializable types to strings
                        if isinstance(val, (datetime.date, datetime.datetime)):
                            val = val.isoformat()
                        filtered[k] = val
                _frontmatter_cache[key] = filtered
                return filtered
        except yaml.YAMLError:
            pass

    _frontmatter_cache[key] = {}
    return {}


def _get_frontmatter_line_range(content: str) -> tuple[int, int] | None:
    """Return ``(start_line, end_line)`` of frontmatter block, 1-indexed.

    Returns ``None`` if the file has no frontmatter.
    """
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return (1, i + 1)
    return None


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------


def resolve_search_paths(
    project_root: Path,
    scope: str,
) -> list[Path]:
    """Resolve search paths for the given *scope*.

    Returns a list of ``Path`` objects relative to *project_root*.
    Uses the symlink path (``Library/``) directly — does NOT resolve
    symlinks, so ``relative_to()`` calls downstream work correctly.

    Raises ``ValueError`` if *scope* is invalid.
    """
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope '{scope}'. Use one of: {', '.join(sorted(VALID_SCOPES))}.")

    rel_paths = SCOPE_PATHS[scope]
    resolved: list[Path] = []
    for rel in rel_paths:
        p = project_root / rel
        if p.is_dir():
            resolved.append(p)
        else:
            logger.warning(f"Search path not found: {p}")
    return resolved


# ---------------------------------------------------------------------------
# Grep execution
# ---------------------------------------------------------------------------


def run_grep(
    query: str,
    search_paths: list[Path],
    engine_path: str,
    engine_name: str,
    project_root: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Run grep / ripgrep and return raw stdout.

    Uses ``-n`` (line numbers) and, for grep, ``-r`` (recursive). rg is
    recursive by default so ``-r`` is omitted (it means ``--replace`` in rg).
    The command is executed with *project_root* as ``cwd`` so output paths
    are relative.

    Raises ``ValueError`` if *query* is empty.
    Raises ``TimeoutError`` if the subprocess exceeds *timeout* seconds.
    Raises ``RuntimeError`` if grep exits with code 2 (error).
    """
    if not query or not query.strip():
        raise ValueError("Query parameter is required.")

    # Build command with relative paths so output is project-relative.
    # NOTE: flag semantics differ between engines!
    #   rg:   `-r` = --replace (takes a value!), `-n` = line numbers
    #         recursive is DEFAULT — do NOT use `-r` unless you want replace
    #   grep: `-r` = recursive, `-n` = line numbers
    #         NOT recursive by default — MUST use `-r`
    if engine_name == "ripgrep":
        cmd = [engine_path, "-n", query]
    else:
        cmd = [engine_path, "-rn", query]

    for p in search_paths:
        try:
            rel = p.relative_to(project_root)
            cmd.append(str(rel))
        except ValueError:
            cmd.append(str(p))

    logger.debug(f"Running: {' '.join(cmd)} (cwd={project_root})")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            cwd=project_root,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"Search timed out after {timeout}s.")

    # Exit codes: 0 = matches found, 1 = no matches, 2 = error
    if result.returncode == 2:
        error_msg = result.stderr.strip() or "Unknown grep error"
        raise RuntimeError(f"Search failed: {error_msg}")

    return result.stdout


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


def parse_search_output(
    raw: str,
    project_root: Path,
    max_results: int,
    context_lines: int,
    no_frontmatter: bool,
) -> str:
    """Parse grep output into a JSON array of result objects.

    Each result object has the shape::

        {
            "path": "Library/Wiki/concepts/2026/05/kb-mcp-design.md",
            "line": 53,
            "snippet": "51-  line before\\n52-  context\\n53:  matched line\\n54-  next line",
            "frontmatter": {"title": "Design KB MCP", "date": "2026-05-20", ...}
        }

    Parameters
    ----------
    raw : str
        Raw stdout from ``grep -rn``.
    project_root : Path
        Root of the repository (used to resolve relative paths).
    max_results : int
        Maximum number of results to return (already clamped).
    context_lines : int
        Number of lines before/after each match to include in snippet.
    no_frontmatter : bool
        If ``True``, skip matches that fall inside the YAML frontmatter block.

    Returns
    -------
    str
        JSON array of result dicts (or ``[]`` if no matches).
    """
    if not raw.strip():
        return "[]"

    results: list[dict[str, Any]] = []

    for line in raw.splitlines():
        if not line.strip():
            continue

        # Parse "path:line_num:content". Use a greedy match for the path
        # since paths may contain colons (e.g. "C:\..."), but on Linux
        # this is safe with the first colon separator.
        colon_idx = line.find(":")
        if colon_idx < 0:
            continue
        path_part = line[:colon_idx]

        rest = line[colon_idx + 1 :]
        colon_idx2 = rest.find(":")
        if colon_idx2 < 0:
            continue

        try:
            line_num = int(rest[:colon_idx2])
        except ValueError:
            continue

        matched_content = rest[colon_idx2 + 1 :]
        rel_path = path_part.strip()

        # Only process .md files
        if not rel_path.lower().endswith(".md"):
            continue

        # Resolve the full path (use resolve_relative to preserve symlink,
        # so relative_to() calls downstream work correctly)
        file_path = resolve_relative(rel_path)
        if not file_path.is_file():
            continue

        key = _get_file_path_key(file_path, project_root)

        # ---- Frontmatter line-range filter ----
        if no_frontmatter:
            if key not in _fm_range_cache:
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    _fm_range_cache[key] = None
                    content = ""
                _fm_range_cache[key] = _get_frontmatter_line_range(content)

            fm_range = _fm_range_cache[key]
            if fm_range and fm_range[0] <= line_num <= fm_range[1]:
                continue

        # ---- Read file lines for snippet ----
        if key not in _file_lines_cache:
            try:
                _file_lines_cache[key] = file_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            except OSError:
                _file_lines_cache[key] = []

        lines = _file_lines_cache[key]
        num_lines = len(lines)

        # Calculate snippet range (0-indexed)
        snippet_start = max(0, line_num - 1 - context_lines)
        snippet_end = min(num_lines, line_num + context_lines)

        # Format snippet with line-number prefixes
        snippet_parts: list[str] = []
        for i in range(snippet_start, snippet_end):
            display_line = i + 1
            prefix = ":" if display_line == line_num else "-"
            snippet_parts.append(f"{display_line}{prefix}{lines[i]}")

        snippet = "\n".join(snippet_parts)

        # ---- Frontmatter for output ----
        fm = get_frontmatter(file_path, project_root)

        results.append(
            {
                "path": rel_path,
                "line": line_num,
                "snippet": snippet,
                "frontmatter": fm,
            }
        )

        if len(results) >= max_results:
            break

    return json.dumps(results, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# High-level convenience
# ---------------------------------------------------------------------------


def search(
    query: str,
    project_root: Path,
    scope: str = "wiki+docs",
    max_results: int = DEFAULT_MAX_RESULTS,
    context_lines: int = DEFAULT_CONTEXT_LINES,
    no_frontmatter: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Run a complete Knowledge Base search end-to-end.

    This is the main entry point for the grep engine. It:

    1. Detects the available grep engine (ripgrep > grep).
    2. Resolves search paths for the requested *scope*.
    3. Runs grep with the given *query*.
    4. Parses output into structured JSON results.

    Parameters
    ----------
    query : str
        Search term (plain text).
    project_root : Path
        Absolute path to the repository root.
    scope : str
        One of ``"wiki"``, ``"docs"``, ``"wiki+docs"``, ``"all"``.
    max_results : int
        Max result objects (clamped to 50).
    context_lines : int
        Lines of context before/after each match (clamped to 10).
    no_frontmatter : bool
        Exclude matches inside YAML frontmatter.
    timeout : int
        Subprocess timeout in seconds.

    Returns
    -------
    str
        JSON string: array of result objects, or an ``{"error": ...}``
        object on failure.
    """
    try:
        engine_name, engine_path = get_grep_engine()
    except RuntimeError as e:
        return json.dumps({"error": str(e)})

    try:
        search_paths = resolve_search_paths(project_root, scope)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    if not search_paths:
        return json.dumps({"error": "No valid search paths found for the given scope."})

    # Clamp
    if max_results > MAX_RESULTS_LIMIT:
        logger.warning(f"max_results clamped from {max_results} to {MAX_RESULTS_LIMIT}")
        max_results = MAX_RESULTS_LIMIT
    if context_lines > MAX_CONTEXT_LINES:
        logger.warning(f"context_lines clamped from {context_lines} to {MAX_CONTEXT_LINES}")
        context_lines = MAX_CONTEXT_LINES

    logger.info(f"search: query='{query[:80]}', scope={scope}, engine={engine_name}")

    try:
        raw = run_grep(query, search_paths, engine_path, engine_name, project_root, timeout)
    except (TimeoutError, RuntimeError, ValueError) as e:
        return json.dumps({"error": str(e)})

    try:
        return parse_search_output(raw, project_root, max_results, context_lines, no_frontmatter)
    except Exception as e:
        logger.exception("Failed to parse grep output")
        return json.dumps({"error": f"Failed to parse results: {e}"})
