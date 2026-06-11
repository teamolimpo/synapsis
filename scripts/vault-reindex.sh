#!/bin/bash
#
# vault-reindex.sh — convenience wrapper
#
# After mounting (or after adding lots of new handoffs/wiki), re-scan the
# private content into the knowledge index.
#
set -euo pipefail

echo "==> Reindexing knowledge from Library/ (Wiki + Handoff + projects etc.)"
echo "    This uses the paths declared in .synapsis/config.yaml"

if [[ ! -e "Library" ]]; then
    echo "ERROR: Library not mounted. Run bash scripts/vault-mount.sh first."
    exit 1
fi

uv run python -m tools.knowledge_base.chunk_indexer update --verbose

echo ""
echo "Done. You can now search private content with higher recall."
echo "Tip: synapsis__search with scope=knowledge or use the /synapsis skill."
