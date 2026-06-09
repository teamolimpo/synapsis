# Synapsis — Commands for Reindexing and Maintenance (for Testing)

This file collects useful commands for **reindexing**, FTS index rebuilds, vacuum, consolidate, and hygiene on Synapsis.

**IMPORTANT UPDATE (knowledge_base integration completed)**:
- `uv run python -m tools.synapsis vacuum` does **NOT** index new files you manually copy into `Library/`. It only performs SQLite maintenance (`VACUUM; REINDEX; ANALYZE;`) on rows that are **already present** in the database.
- The real tool to scan `.md` files (Wiki, Handoffs, ...) and populate `chunks` / `chunks_fts` / `file_state` (with SHA256 change detection) is now **native** to this repository:
  `uv run python -m tools.knowledge_base.chunk_indexer update|rebuild|clean`
- `synapsis__admin(act="index", ix="...")` works without import errors.
- Chunks now live inside the main `.synapsis/synapsis.db` (no longer a separate `chunks.db`).

## 1. Synapsis CLI (core database maintenance)

```bash
cd /path/to/synapsis

# SQLite maintenance (space + internal indexes) — does NOT discover new files on disk
uv run python -m tools.synapsis vacuum

uv run python -m tools.synapsis hygiene --apply
uv run python -m tools.synapsis stats --json
uv run python -m tools.synapsis compress --warm --apply
```

## 2. Reindexing Library files (chunk knowledge) — now native

The tool to scan `.md` files in the directories you explicitly list under `knowledge.include` in `.synapsis/config.yaml` (typically Wiki + Handoff, plus any additional project folders you want) and populate `chunks` + `chunks_fts` + `file_state` (with SHA256 change detection) is **integrated** into this repository.

```bash
cd /path/to/synapsis

# Incremental update (recommended for daily use)
uv run python -m tools.knowledge_base.chunk_indexer update

# Full rebuild from scratch (drops the kb tables and recreates them)
uv run python -m tools.knowledge_base.chunk_indexer rebuild

# Remove orphan chunks (files that no longer exist on disk)
uv run python -m tools.knowledge_base.chunk_indexer clean

# Dry-run + verbose (safe to try)
uv run python -m tools.knowledge_base.chunk_indexer update --dry-run -v
```

These functions scan the paths you explicitly list under `knowledge.include` in `.synapsis/config.yaml`.

**Convenience CLI for configuration**

Instead of hand-editing the YAML you can use:

```bash
uv run python -m tools.synapsis knowledge init          # create starter with Wiki + Handoff
uv run python -m tools.synapsis knowledge list          # (or `ls`) show current include/exclude + existence check
uv run python -m tools.synapsis knowledge add Library/projects/
uv run python -m tools.synapsis knowledge remove Library/projects/
uv run python -m tools.synapsis knowledge exclude "**/*-draft*"
uv run python -m tools.synapsis knowledge unexclude "**/*-draft*"
```

After `init` or `add`, run the indexer as usual (`uv run python -m tools.knowledge_base.chunk_indexer update` or the equivalent via `synapsis__admin`).

The hard error you get when `knowledge.include` is missing/empty now recommends these real commands.

**Indexing Configuration (`.synapsis/config.yaml`)**

Synapsis uses a single local configuration file for operational settings:

```yaml
# .synapsis/config.yaml
# General synapsis configuration file (inside the hot store, gitignored).

knowledge:
  # "The opposite of .gitignore": positive list of what you want to index.
  # Paths are relative to the project root.
  include:
    - Library/Wiki/
    - Library/Handoff/
    # - Library/notes/
    # - Library/SOPs/

  # Exclusion patterns in .gitignore style (applied after the include list).
  # Useful for drafts, archives, private material, etc.
  exclude:
    - Library/Archive/**
    - "**/*-draft*"
    - "**/.private/**"

  # Heading levels to split chunks on (1 = #, 2 = ##, ...)
  heading_levels: [2, 3]

  # Optional: different embedding model (only if you installed the embeddings extra)
  # embedding_model: "sentence-transformers/all-MiniLM-L6-v2"

  # Optional: custom entity dictionary
  # entity_dictionary: "Library/System/my-entities.yaml"
```

**Explicit configuration is required (no more silent defaults)**

`knowledge.include` must be explicitly declared in `.synapsis/config.yaml`.

If the list is missing or empty, or if any declared path does not exist on disk, the indexer commands (`update`, `rebuild`, `clean`) will **fail immediately** with a clear error message that tells you exactly what to do.

This is the intended behaviour: first use / first call produces an actionable alarm instead of surprising you with partial or zero results.

Example minimal config:

```yaml
knowledge:
  include:
    - Library/Wiki/
    - Library/Handoff/
```

See `Documents/examples/synapsis-config.yaml` for the full commented template.

Missing directories are no longer silently skipped — they cause an error so you notice the problem right away.

A complete, well-commented example file is available at `Documents/examples/synapsis-config.yaml`.

**Notes on paths and the database (after integration):**
- `Library/` lives at the project root; symlinks to external vaults continue to work thanks to `tools/common/paths.py`.
- Chunks (and their state) live **inside the main database** `.synapsis/synapsis.db` (the same one used by sessions, tasks, handoffs, and observations).
- Path resolution is centralized in `tools.common.paths.resolve_synapsis_db()` (respects `SYNAPSIS_DB_PATH`).
- No more PYTHONPATH hacks, sibling checkouts, or workarounds: `uv run python -m tools.knowledge_base.chunk_indexer ...` works directly.
- For a complete configuration file example see `Documents/examples/synapsis-config.yaml`.

## 3. Tramite MCP / admin (ora pienamente funzionale)

```json
use_tool synapsis__admin { "act": "index", "ix": "status" }
use_tool synapsis__admin { "act": "index", "ix": "update" }     # incrementale + embeddings/entities se presenti
use_tool synapsis__admin { "act": "index", "ix": "rebuild" }   # full (ricrea tabelle kb)
use_tool synapsis__admin { "act": "index", "ix": "clean" }
```

Lo stub in `server.py` che faceva `from tools.knowledge_base import chunk_indexer` ora riesce perché il package è copiato e adattato dentro `tools/knowledge_base/`. Il wrapper chiama direttamente `update/rebuild/clean`. `ix=status` funziona anche senza il modulo (query diretto su `chunks`/`file_state`).

## 4. Handoff vs Knowledge Chunks

- **Handoff** (Library/Handoff/...): to be correctly entered into the `hf` + `hf_fts` tables (and therefore be discoverable with `scope=hf`), handoffs **must** be created using `synapsis__hf(act="new")` or the `/handoff` skill. Simply copying the .md file to disk does not populate the database.
- **Knowledge chunks** (full-text / semantic search over file content): managed by the `chunk_indexer` above (populates `chunks` / `chunks_fts`).

`vacuum` does not replace either of these.

## 5. Explicit FTS Index Rebuild (Full-Text Search)

Synapsis uses FTS5 on several virtual tables. To force a complete rebuild (without scanning any new files):

```python
from tools.synapsis.store import SynapsisStore
store = SynapsisStore()
for t in ["hf", "observations", "tasks", "chunks"]:
    store._conn.execute(f"INSERT INTO {t}_fts({t}_fts) VALUES('rebuild')")
store._conn.commit()
store.vacuum()
store.close()
```

One-liner (from this repo):

```bash
uv run python -c '
from tools.synapsis.store import SynapsisStore
s=SynapsisStore()
for t in ["hf","observations","tasks","chunks"]:
    s._conn.execute(f"INSERT INTO {t}_fts({t}_fts) VALUES(\"rebuild\")")
s._conn.commit()
print(s.vacuum())
s.close()
'
```

## Quick Summary for "Reindexing Everything"

| Purpose                            | Command / Call                                               | Indexes new files in Library? | Notes |
|------------------------------------|--------------------------------------------------------------|--------------------------------|-------|
| Existing SQLite maintenance        | `uv run python -m tools.synapsis vacuum`                     | **No**                         | Only REINDEX on already-present data |
| FTS rebuild on existing tables     | `INSERT INTO *_fts(*_fts) VALUES('rebuild')`                 | No                             | hf / observations / tasks / chunks |
| Knowledge chunks (.md files)       | `uv run python -m tools.knowledge_base.chunk_indexer update` | **Yes** (incremental)          | Native in synapsis (uses .synapsis/synapsis.db + local Library/) |
| Full knowledge chunks rebuild      | `uv run python -m tools.knowledge_base.chunk_indexer rebuild` | **Yes** (from scratch)        | Recreats only kb tables; embeddings/entities if the extra is installed |
| Via MCP                            | `synapsis__admin(act="index", ix="update"\|"rebuild"\|"status"\|"clean")` | Yes                   | Works (import is no longer a stub) |
| Hygiene / consolidate              | `uv run python -m tools.synapsis hygiene --apply`            | No                             | Only observations / memory layers |

---

**File generated for testing** — `Documents/synapsis-commands.md` (both in `~/Documents/` and in the repo).  
See sources: `tools/synapsis/...`, `tools/knowledge_base/...`, `tools/common/paths.py`.