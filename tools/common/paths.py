"""Condivisione path di progetto per tutti i tool Team Olimpo / synapsis.

Fornisce tre funzioni per risolvere i path in modo consistente,
gestendo correttamente symlink (principalmente per `Library/` quando
viene puntato a un vault esterno; `.synapsis/` è invece il default
low-latency locale per il DB operativo).

* :func:`project_root` — radice del repository (via ``Path(__file__)`` resolution)
* :func:`resolve_relative` — join con ``project_root`` **senza** risolvere symlink
* :func:`resolve_absolute` — join con ``project_root`` **con** risoluzione symlink

Usage::

    from tools.common.paths import project_root, resolve_relative, resolve_absolute

    root = project_root()                    # e.g. /path/to/synapsis
    rel  = resolve_relative("Library")       # .../synapsis/Library (symlink preserved)
    abs  = resolve_absolute("Library")       # .../real-vault (symlinks resolved for I/O)
    db   = resolve_absolute(".synapsis/synapsis.db")
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

    Honors the SYNAPSIS_DB_PATH env var (absolute, or relative to project root).
    Default: .synapsis/synapsis.db (resolved via project_root + absolute rules).

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
        return resolve_absolute(str(p))
    return resolve_absolute(".synapsis/synapsis.db")
