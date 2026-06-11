"""Condivisione path di progetto per tutti i tool Team Olimpo / synapsis.

Fornisce tre funzioni per risolvere i path in modo consistente,
gestendo correttamente symlink (principalmente per `Library/` quando
viene puntato a un vault esterno; `.synapsis/` è invece il default
low-latency locale per il DB operativo).

* :func:`project_root` — radice del repository (via ``Path(__file__)`` resolution)
* :func:`resolve_relative` — join con ``project_root`` **senza** risolvere symlink
* :func:`resolve_absolute` — join con ``project_root`` **con** risoluzione symlink
* :func:`ensure_vault_mounted` — **safety guard** (chiama prima di scrivere in Library/)

Usage::

    from tools.common.paths import (
        project_root, resolve_relative, resolve_absolute, ensure_vault_mounted
    )

    root = project_root()
    rel  = resolve_relative("Library")       # lexical (symlink preserved)
    abs  = resolve_absolute("Library")       # real vault path
    db   = resolve_absolute(".synapsis/synapsis.db")

    vault = ensure_vault_mounted()           # raises clear error with the exact
                                             # "comando semplicissimo" if not ready
"""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT: Path | None = None


def project_root() -> Path:
    """Return the absolute project root path (cache after first call).

    Discovery strategy: walk up from ``tools/common/paths.py`` to find the
    ``tools/`` parent directory. This is reliable because this module lives at
    ``tools/common/paths.py``, exactly three levels below the project root.

    Returns:
        Absolute path to the repository root (e.g. ``/path/to/synapsis``).
    """
    global _PROJECT_ROOT  # noqa: PLW0603
    if _PROJECT_ROOT is None:
        _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    return _PROJECT_ROOT


# ---------------------------------------------------------------------------
# Plugin / library mode support (minimo for marketplace distribution)
# When synapsis runs as a Grok plugin (GROK_PLUGIN_ROOT in env or path under
# ~/.grok/plugins or marketplace-cache), user data (DB, Library) must resolve
# relative to the *consumer's* workspace (CWD), not the plugin checkout.
# project_root() is kept unchanged: it always reports the synapsis package root.
# ---------------------------------------------------------------------------

_PLUGIN_MARKERS = (".grok/plugins", "installed-plugins", "marketplace-cache", ".claude/plugins")


def _is_plugin_context() -> bool:
    """Detect if the synapsis code is being executed from a Grok plugin install."""
    # Strong signal: Grok sets these when launching MCPs / hooks from plugins
    if os.environ.get("GROK_PLUGIN_ROOT") or os.environ.get("GROK_PLUGIN_DATA"):
        return True
    mod = str(Path(__file__).resolve())
    if any(marker in mod for marker in _PLUGIN_MARKERS):
        return True
    # Extra safety for installed plugin layouts: if the module lives under ~/.grok and contains "synapsis"
    # treat it as plugin context so we always prefer consumer CWD for data.
    if ".grok" in mod and "synapsis" in mod.lower():
        return True
    return False


def _discover_workspace_root() -> Path | None:
    """Walk upward from CWD for a consumer project root (git, .grok, pyproject...).
    Returns None if nothing sensible is found (fallback will apply).
    """
    start = Path.cwd().resolve()
    for p in [start] + list(start.parents):
        if (p / ".git").exists() or (p / ".grok").exists() or (p / "pyproject.toml").exists():
            return p
    return None


def workspace_root() -> Path:
    """Active workspace for user data (DB, Library, knowledge, etc.).

    In plugin context: prefer discovery from the current working directory
    (i.e. the project that installed/uses the synapsis plugin).
    Otherwise (or if discovery fails): fall back to the classic module-based
    project_root() so development inside the synapsis clone continues to work.
    """
    if _is_plugin_context():
        ws = _discover_workspace_root()
        if ws is not None:
            return ws
        # In plugin mode, if no project marker found in ancestor tree, fall back to the
        # immediate current working directory as the workspace. This prevents accidentally
        # writing DB/Library/handoffs into the plugin installation dir for bare test dirs
        # or projects without .git/.grok/pyproject.toml.
        return Path.cwd().resolve()
    return project_root()


def resolve_relative(*parts: str) -> Path:
    """Join *parts* with :func:`project_root` — does **not** resolve symlinks.

    Use this when the resulting path must remain under ``project_root`` for
    operations like :meth:`~pathlib.Path.relative_to`, cache keys, or
    arguments passed to subprocesses (e.g. ``ripgrep``).

    Args:
        *parts: Path segments to join after ``project_root``.

    Returns:
        A ``Path`` that is ``project_root / joined_parts`` **without**
        calling ``.resolve()``, so symlinks (e.g. Library) are preserved.
    """
    return project_root().joinpath(*parts)


def resolve_absolute(*parts: str) -> Path:
    """Join *parts* with :func:`project_root` and resolve **all** symlinks.

    Use this for actual I/O operations (``read_text``, ``write_text``,
    ``is_file``, ``is_dir``, ``exists``) so that the real filesystem path
    is used.

    Args:
        *parts: Path segments to join after ``project_root``.

    Returns:
        A ``Path`` with all symlinks resolved (useful when Library is symlinked
        to an external vault; .synapsis/ is normally local and does not need this).
    """
    return project_root().joinpath(*parts).resolve()


def resolve_synapsis_db() -> Path:
    """Return the primary Synapsis DB path for store + knowledge chunks.

    Honors the SYNAPSIS_DB_PATH env var (absolute, or relative to workspace).
    Default: .synapsis/synapsis.db resolved from workspace_root() (plugin-aware:
    uses the consumer project CWD when synapsis is loaded from a Grok plugin;
    falls back to classic project_root for development inside the synapsis tree).

    This is the single source of truth used by SynapsisStore and by the
    integrated knowledge_base.chunk_indexer (after adaptation). It keeps
    chunks inside the main synapsis.db rather than a legacy separate chunks.db
    or a legacy external layout.

    Returns:
        Absolute Path to the SQLite DB file.
    """
    env_path = os.environ.get("SYNAPSIS_DB_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_absolute():
            return p
        # relative env still resolved against the *active* workspace
        return workspace_root().joinpath(str(p)).resolve()
    return workspace_root().joinpath(".synapsis/synapsis.db").resolve()


def ensure_vault_mounted() -> Path:
    """Ensure that Library/ is mounted (symlink or valid dir) to the private vault.

    This is the central safety guard. It prevents code from silently creating
    a *real* directory named Library/ inside the public clone when the external
    symlink is not present.

    In plugin context the check is performed against the *consumer workspace*
    (so a project that adopts the full synapsis discipline + vault will work
    even when the memory engine is provided by the installed plugin).

    Creating a real Library/ would be:
    - gitignored (so "invisible" in status)
    - but pollute the public working tree
    - block future `ln -s` (you'd have to rm -rf first)
    - cause all handoffs/wiki to be written to a local-only location instead
      of the shared private vault → data loss for the team.

    Call this **before any write** that expects durable private storage
    (handoff files, wiki contributions, etc.).

    The error message tells the user the exact "comando semplicissimo" to run.

    Returns:
        Absolute resolved path to the vault root (after following the symlink).
    """
    ws = workspace_root()
    lib = ws.joinpath("Library")

    # Heuristic: are we running "inside" the synapsis package itself?
    # (developing the source or the plugin checkout is the current workspace)
    # In that case keep the strict "must have proper vault symlink" policy.
    is_self = (ws == project_root())

    if not lib.exists():
        if is_self:
            # Strict protection only for the canonical synapsis source tree
            raise RuntimeError(
                "VAULT NOT MOUNTED: Library/ does not exist.\n\n"
                "To be subito ready with your private work tool (full handoffs, "
                "private knowledge, projects/, etc.):\n\n"
                "  1. Clone the private vault (once):\n"
                "       git clone https://github.com/teamolimpo/synapsis-vault.git ~/synapsis-vault\n\n"
                "  2. Inside the public clone, run ONE of the following simple commands:\n"
                "       synapsis vault mount\n"
                "       bash scripts/vault-mount.sh\n\n"
                "This creates the external symlink (Library -> your vault) and prepares .synapsis/.\n"
                "After that, /handoff and private search will work durably."
            )
        else:
            # Plugin / consumer project usage: auto-create a local Library/
            # so that handoffs and wiki just work without manual mkdir or vault setup.
            lib.mkdir(parents=True, exist_ok=True)
            note = lib / "README.md"
            if not note.exists():
                note.write_text(
                    "# Local Library (auto-created)\n\n"
                    "This Library/ was created automatically because you are using the\n"
                    "synapsis plugin in a project that does not have a mounted private vault.\n\n"
                    "Handoff files and Wiki contributions will be stored locally under this\n"
                    "directory only (gitignored by default in most setups).\n\n"
                    "If you later want the full durable/shared vault experience:\n"
                    "  - Clone your vault\n"
                    "  - Run the mount commands (synapsis vault mount or the scripts)\n"
                    "  - Or manually replace this dir with a symlink to the vault.\n\n"
                    "You can safely delete this directory if you don't want local handoffs.\n"
                )

    if lib.is_file():
        raise RuntimeError(
            f"Library exists but is a plain file instead of a directory or symlink: {lib}\n"
            "Remove the file and re-run the mount command."
        )

    # Success: return the real (resolved) path for I/O.
    # In plugin mode this will be the consumer workspace's Library.
    return lib.resolve()
