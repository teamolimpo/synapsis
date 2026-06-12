"""Project path sharing for all Team Olimpo / synapsis tools.

Provides functions to resolve paths consistently, correctly handling
symlinks (primarily for `Library/` when it points to an external vault;
`.synapsis/` is the default low-latency local store for the operational DB).

Two distinct concepts (to support "synapsis as a Grok plugin"):

* :func:`project_root` — root of the synapsis package (via ``Path(__file__)``).
  Used for internal code / "is_self" checks, not for user data.
* :func:`workspace_root` — active root of the *consumer project* (where
  .synapsis/, Library/, knowledge etc. live). In plugin context it discovers
  from CWD (skipping the plugin package itself) + env overrides.
* :func:`resolve_relative` / :func:`resolve_absolute` — now based on workspace_root
  (plugin-aware); join for data/config with/without symlink resolution.
* :func:`ensure_vault_mounted` — **safety guard** (call before writing to Library/)

Usage::

    from tools.common.paths import (
        project_root, workspace_root, resolve_relative, resolve_absolute, ensure_vault_mounted
    )

    pkg  = project_root()                    # where the synapsis code lives
    ws   = workspace_root()                  # where the open project's data lives
    rel  = resolve_relative("Library")       # lexical (symlink preserved)
    abs  = resolve_absolute("Library")       # real vault path
    db   = resolve_absolute(".synapsis/synapsis.db")

    vault = ensure_vault_mounted()           # raises clear error with the exact
                                             # quick vault mount command if not ready
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
# Plugin / library mode support (minimal for marketplace distribution)
# When synapsis runs as a Grok plugin (GROK_PLUGIN_ROOT in env or path under
# ~/.grok/plugins or marketplace-cache), user data (DB, Library) must resolve
# relative to the *consumer's* workspace (CWD), not the plugin checkout.
# project_root() is kept unchanged: it always reports the synapsis package root.
# ---------------------------------------------------------------------------
_PLUGIN_MARKERS = (".grok/plugins", "installed-plugins", "marketplace-cache", ".claude/plugins", "synapsis-")


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


def _is_synapsis_package(p: Path) -> bool:
    """Return True if *p* looks like the root of the synapsis package/plugin itself.

    Used to prevent workspace_root() from accidentally selecting the plugin
    installation directory (which ships with .grok/ + pyproject.toml + plugin.json)
    as the consumer workspace for .synapsis/ and Library/.
    """
    if (p / "plugin.json").exists():
        return True
    if (p / "tools" / "synapsis" / "server.py").exists():
        return True
    pp = p / "pyproject.toml"
    if pp.exists():
        try:
            txt = pp.read_text(encoding="utf-8", errors="ignore")
            if 'name = "synapsis"' in txt or "name = 'synapsis'" in txt:
                return True
        except Exception:
            pass
    return False


def _is_grok_global_config_dir(p: Path) -> bool:
    """Return True if *p* looks like the user's global Grok app data/config root.

    This contains things like installed-plugins/, bundled/, active_sessions.json etc.
    We must never treat Grok's own global state directory as a "consumer project"
    workspace for .synapsis/ or Library/ data.

    A legitimate consumer project may have its own (local) .grok/ for skills/hooks,
    but that local .grok/ will not contain the "installed-plugins" subtree.
    """
    try:
        rp = p.resolve()
        # Direct signals of global Grok data home
        if (rp / ".grok" / "installed-plugins").exists():
            return True
        if (rp / ".grok" / "bundled").exists():
            return True
        if (rp / ".grok" / "active_sessions.json").exists():
            return True
        # If we are at $HOME (or similar) and .grok exists with plugin installs
        home = Path.home().resolve()
        if rp == home or str(rp).startswith(str(home) + "/"):
            if (rp / ".grok" / "installed-plugins").exists():
                return True
    except Exception:
        pass
    return False


def _discover_workspace_root(start: Path | None = None) -> Path | None:
    """Walk upward from the given start (or CWD) for a consumer project root.

    Looks for .git, .grok directory, or pyproject.toml.
    Explicitly skips:
      - the synapsis package/plugin itself
      - global Grok configuration roots (e.g. the dir that owns ~/.grok/installed-plugins)

    NOTE: in pure plugin/consumer usage we often short-circuit and just trust the
    launch dir (PWD) directly instead of walking, to support bare dirs and avoid
    accidentally climbing into the global Grok config tree.
    """
    if start is None:
        start = Path.cwd().resolve()
    else:
        start = start.resolve()
    for p in [start] + list(start.parents):
        if (p / ".git").exists() or (p / ".grok").exists() or (p / "pyproject.toml").exists():
            if _is_synapsis_package(p):
                continue
            if _is_grok_global_config_dir(p):
                continue
            return p
    return None


def workspace_root() -> Path:
    """Active workspace for user data (DB, Library, knowledge, etc.).

    Per official Grok Build documentation, the canonical signal for the
    working directory (the directory from which `grok` was launched / the
    root of the current workspace) is the environment variable
    `GROK_WORKSPACE_ROOT` (with `CLAUDE_PROJECT_DIR` as a compatible alias).
    These are injected by the runner for hooks and are the authoritative value
    that plugin/MCP code should use when it needs to write files inside the
    user's launch working directory (e.g. Library/Handoff for handoffs).

    This function therefore treats `GROK_WORKSPACE_ROOT` / `CLAUDE_PROJECT_DIR`
    as the *highest priority / primary source of truth* and returns it
    immediately when present. All other discovery (cwd walking, markers, plugin
    detection) is only a fallback when the official env is absent.

    The previous SYNAPSIS_WORKSPACE alias is still supported for manual
    overrides / tests.

    In Grok plugin context (MCP servers loaded via .mcp.json from an installed
    synapsis plugin), the host often does not (or did not) inject GROK_WORKSPACE_ROOT
    into the MCP child env. In that case we rely on the PWD env var (which the
    host preserves as the user's launch dir) + strong preference for that launch
    dir as the data workspace. We explicitly refuse to return global Grok config
    dirs or the plugin install dir itself.
    """
    _log_path_debug(
        "workspace_root-entry",
        cwd=str(Path.cwd()),
        pwd=os.environ.get("PWD"),
        grok_ws_raw=os.environ.get("GROK_WORKSPACE_ROOT"),
        claude_raw=os.environ.get("CLAUDE_PROJECT_DIR"),
        grok_plugin_root=os.environ.get("GROK_PLUGIN_ROOT"),
        plugin_ctx=_is_plugin_context(),
    )

    # Official Grok signal first (GROK_WORKSPACE_ROOT + Claude alias).
    # Only accept real expanded absolute paths. The .mcp.json may contain the
    # literal template "${GROK_WORKSPACE_ROOT}" (Grok did not expand it for the
    # plugin's MCP registration in this run). We ignore such values.
    for env_key in ("GROK_WORKSPACE_ROOT", "CLAUDE_PROJECT_DIR", "SYNAPSIS_WORKSPACE", "GROK_WORKSPACE"):
        env_val = os.environ.get(env_key)
        if env_val and not env_val.startswith("${") and env_val.startswith("/"):
            p = Path(env_val).resolve()
            _log_path_debug("official-env", env_key=env_key, chosen=str(p))
            return p

    # Strong practical signal from the user's shell: PWD at the moment `grok` was launched.
    # In the reported failure, os.getcwd() inside the MCP was the plugin dir (due to
    # "uv --directory ${GROK_PLUGIN_ROOT}"), but the PWD env was correctly the user's
    # launch directory. We treat PWD as the intended workspace for data in plugin mode.
    pwd = os.environ.get("PWD")
    pwd_path = Path(pwd).resolve() if pwd else None
    cwd_path = Path.cwd().resolve()
    launch = pwd_path if (pwd_path and pwd_path.exists()) else cwd_path

    pkg = project_root()

    # If we appear to be running inside the synapsis development tree itself
    # (dev on the plugin, or testing the source tree "as a plugin" via its local
    # .mcp.json), the workspace for .synapsis/ and Library/ is the tree.
    # This must take precedence even if _is_plugin_context() is True because
    # GROK_PLUGIN_ROOT pointed at the source.
    if (launch == pkg or pkg in list(launch.parents)[:3] or launch in list(pkg.parents)[:1]):
        if (pkg / "tools" / "synapsis" / "server.py").exists() and (pkg / "plugin.json").exists():
            # Confirmed: we are operating on the synapsis source tree (dev or self-test).
            # Do not escape to parent; use the tree as both package and workspace.
            _log_path_debug("dev-inside-source-tree", chosen=str(pkg), launch=str(launch))
            return pkg

    if _is_plugin_context():
        # Plugin / installed usage in a *consumer* project.
        # Primary rule (post-merge regression fix): the user's launch directory
        # (what they had open / `cd`ed into before running grok) **is** the workspace
        # for synapsis data, unless that dir *is* the plugin install tree.
        # We intentionally do **not** do greedy ancestor walking here, because
        # that caused bare dirs under $HOME (or any dir without .git/.grok/pyproject)
        # to climb all the way to the global ~/.grok owner and create .synapsis/
        # and Library/ in the wrong place.
        if _is_synapsis_package(launch) or _is_grok_global_config_dir(launch):
            # Rare: somehow the launch dir itself was the plugin tree or global root.
            # Climb the minimum to reach a non-plugin, non-global ancestor.
            for anc in launch.parents:
                if not _is_synapsis_package(anc) and not _is_grok_global_config_dir(anc):
                    _log_path_debug("plugin-escape-from-bad-launch", chosen=str(anc), launch=str(launch))
                    return anc
            home = Path.home().resolve()
            _log_path_debug("plugin-escape-home", chosen=str(home))
            return home

        # Happy path for consumer projects (including bare dirs, temp test dirs,
        # projects that don't have .git yet, etc.):
        # Trust the launch dir directly.
        _log_path_debug("plugin-launch-dir-trusted", chosen=str(launch), used_pwd=bool(pwd_path))
        return launch

    # Non-plugin dev context (classic): inside the synapsis clone without
    # GROK_PLUGIN_* signals, fall back to the package root computed from __file__.
    _log_path_debug("dev-project-root", chosen=str(pkg))
    return pkg


def _log_path_debug(reason: str, **extra: object) -> None:
    """Best-effort diagnostic for why the workspace resolved this way.

    Appends a single JSON line to /tmp/synapsis-path-debug.log (world-readable
    in /tmp). The user can `cat /tmp/synapsis-path-debug.log` (or the file with
    pid suffix if you extend it) right after reproducing a handoff to see
    exactly what the MCP process saw: cwd, relevant env vars (especially the
    official GROK_WORKSPACE_ROOT), __file__, plugin context, chosen value and
    the reason.

    Never raises and has zero dependencies beyond the stdlib already imported.
    """
    try:
        import datetime as _dt
        import json as _json
        rec = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "pid": os.getpid(),
            "reason": reason,
            "cwd": str(Path.cwd()),
            "grok_workspace_root": os.environ.get("GROK_WORKSPACE_ROOT"),
            "claude_project_dir": os.environ.get("CLAUDE_PROJECT_DIR"),
            "grok_plugin_root": os.environ.get("GROK_PLUGIN_ROOT"),
            "grok_plugin_data": os.environ.get("GROK_PLUGIN_DATA"),
            "pwd_env": os.environ.get("PWD"),
            "module_file": str(Path(__file__).resolve()) if "__file__" in globals() else None,
            **{k: str(v) for k, v in extra.items()},
        }
        line = _json.dumps(rec, ensure_ascii=False) + "\n"
        with open("/tmp/synapsis-path-debug.log", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # Diagnostics must never break path resolution or handoff writes.
        pass

def resolve_relative(*parts: str) -> Path:
    """Join *parts* with :func:`workspace_root` — does **not** resolve symlinks.

    This is the preferred helper for data paths that belong to the *active
    consumer workspace* (the project using synapsis, which may be different
    from the synapsis package when loaded as a Grok plugin).

    Use this when the resulting path must remain under the workspace for
    operations like :meth:`~pathlib.Path.relative_to`, config files,
    knowledge include roots, etc.

    In non-plugin (dev) usage this is equivalent to the old project_root behaviour.

    Args:
        *parts: Path segments to join after the active workspace root.

    Returns:
        A ``Path`` that is ``workspace_root / joined_parts`` **without**
        calling ``.resolve()``, so symlinks (e.g. Library) are preserved.
    """
    return workspace_root().joinpath(*parts)


def resolve_absolute(*parts: str) -> Path:
    """Join *parts* with :func:`workspace_root` and resolve **all** symlinks.

    Plugin-aware equivalent of the old project-root version.
    Use this for actual I/O operations (``read_text``, ``write_text``,
    ``is_file``, ``is_dir``, ``exists``) so that the real filesystem path
    is used.

    Args:
        *parts: Path segments to join after the active workspace root.

    Returns:
        A ``Path`` with all symlinks resolved (useful when Library is symlinked
        to an external vault; .synapsis/ is normally local and does not need this).
    """
    return workspace_root().joinpath(*parts).resolve()


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

    The error message tells the user the exact quick vault mount command to run.

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
                "To quickly set up your private work tool (full handoffs, "
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
