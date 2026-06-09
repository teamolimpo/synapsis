# Synapsis — Comandi per Reindicizzare e Manutenzione (per Test)

Questo file raccoglie i comandi utili per **reindicizzare**, rebuild degli indici FTS, vacuum, consolidate e hygiene su Synapsis.

**ATTENZIONE IMPORTANTE** (aggiornato dopo verifica):
- `uv run python -m tools.synapsis vacuum` **NON indicizza nuovi file** che copi manualmente in `Library/`.
- Fa solo manutenzione SQLite (`VACUUM; REINDEX; ANALYZE;`) sulle righe **già presenti** nelle tabelle del DB.
- Per far entrare nuovi file di Library (Wiki, Handoff, documents, projects) nella ricerca knowledge servono i comandi del `chunk_indexer`.

## 1. CLI Synapsis (manutenzione DB core)

```bash
cd /home/stra/synapsis

# Manutenzione SQLite (spazio + indici interni) — NON scopre file nuovi su disco
uv run python -m tools.synapsis vacuum

uv run python -m tools.synapsis hygiene --apply
uv run python -m tools.synapsis stats --json
uv run python -m tools.synapsis compress --warm --apply
```

## 2. Il comando dedicato per reindicizzare i file di Library (chunk knowledge)

Il vero tool per scansionare `.md` in Library e popolare `chunks` + `chunks_fts` + `file_state` (con change detection via hash) è:

**Dal modulo completo (in /home/stra/TeamOlimpo):**

```bash
# Incremental: indicizza solo file nuovi o modificati (consigliato)
uv run python -m tools.knowledge_base.chunk_indexer update

# Full reindex da zero (cancella tutto e ricrea)
uv run python -m tools.knowledge_base.chunk_indexer rebuild

# Pulisce chunk orfani (file non più su disco)
uv run python -m tools.knowledge_base.chunk_indexer clean
```

Queste funzioni scansionano (di default):
- Library/Wiki/
- Library/documents/
- Library/Handoff/
- Library/projects/

**Nota sui path/DB:**
- Nel setup TeamOlimpo completo usa symlink e risolve `Library/` verso `/home/stra/Library/`, con DB in `Library/System/Poros/synapsis.db`.
- In questo repo `synapsis` standalone la Library è locale (`/home/stra/synapsis/Library/...`) e i chunk vivono dentro `.synapsis/synapsis.db`.
- Per usarlo da qui potresti aver bisogno di PYTHONPATH + adattamenti.

## 3. Tramite MCP (quando il modulo è raggiungibile)

```json
use_tool synapsis__admin { "act": "index", "ix": "status" }
use_tool synapsis__admin { "act": "index", "ix": "update" }     # incrementale
use_tool synapsis__admin { "act": "index", "ix": "rebuild" }   # full
use_tool synapsis__admin { "act": "index", "ix": "clean" }
```

Al momento in questo workspace l'import `from tools.knowledge_base import chunk_indexer` fallisce (il modulo vive in TeamOlimpo).

## 4. Handoff vs Knowledge Chunks

- **Handoff** (Library/Handoff/...): per entrare correttamente nella tabella `hf` + `hf_fts` (e quindi essere trovati con scope `hf`) **devono** essere creati con `synapsis__hf(act="new")` o la skill `/handoff`. Copiare solo il .md sul disco non popola il DB.
- **Knowledge chunks** (ricerca full-text/semantica su contenuto file): gestiti dal `chunk_indexer` sopra (popola `chunks`/`chunks_fts`).

`vacuum` non sostituisce nessuno dei due.

## 5. Rebuild esplicito degli indici FTS (Full-Text Search)

Synapsis usa FTS5 su diverse tabelle virtuali. Per forzare il rebuild completo (senza scansionare file nuovi):

```python
from tools.synapsis.store import SynapsisStore
store = SynapsisStore()
for t in ["hf", "observations", "tasks", "chunks"]:
    store._conn.execute(f"INSERT INTO {t}_fts({t}_fts) VALUES('rebuild')")
store._conn.commit()
store.vacuum()
store.close()
```

One-liner (da questo repo):

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

## Riepilogo rapido "per reindicizzare tutto"

| Scopo                              | Comando / Chiamata                                           | Indicizza file nuovi in Library? | Note |
|------------------------------------|--------------------------------------------------------------|----------------------------------|------|
| Manutenzione SQLite (esistente)    | `uv run python -m tools.synapsis vacuum`                     | **No**                           | Solo REINDEX su dati già nel DB |
| FTS rebuild su tabelle esistenti   | `INSERT INTO *_fts(*_fts) VALUES('rebuild')`                 | No                               | hf / observations / tasks / chunks |
| Knowledge chunks (file .md)        | `uv run python -m tools.knowledge_base.chunk_indexer update`        | **Sì** (incrementale)            | Lo strumento vero per Library/ |
| Knowledge chunks full              | `uv run python -m tools.knowledge_base.chunk_indexer rebuild`       | **Sì** (da zero)                 | Cancella e ricrea tutto |
| Via MCP (quando disponibile)       | `synapsis__admin(act="index", ix="update"\|"rebuild")`       | Sì                               | Wrapper sul chunk_indexer |
| Hygiene / consolidate              | `uv run python -m tools.synapsis hygiene --apply`            | No                               | Solo osservazioni/memory layers |

---

**File generato per test** — `Documents/synapsis-commands.md` (sia in `~/Documents/` che in repo).  
Vedi sorgenti: `tools/synapsis/...`, `/home/stra/TeamOlimpo/tools/knowledge_base/chunk_indexer.py`, `tools/common/paths.py`.