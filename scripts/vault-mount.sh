#!/bin/bash
#
# vault-mount.sh — the quick one-command vault mount
#
# Creates the external symlink Library -> your private vault
# and prepares the local hot .synapsis/ dir.
#
# Usage:
#   bash scripts/vault-mount.sh
#   bash scripts/vault-mount.sh /path/to/your/vault
#   VAULT_PATH=/custom/path bash scripts/vault-mount.sh
#
# After this you are ready to go with handoffs, private knowledge, etc.
#
set -euo pipefail

DEFAULT_VAULT="$HOME/synapsis-vault"
VAULT_PATH="${1:-${VAULT_PATH:-$DEFAULT_VAULT}}"

echo "==> Preparing synapsis private vault mount"
echo "    Target vault: $VAULT_PATH"

if [[ ! -d "$VAULT_PATH" ]]; then
    echo "ERROR: Vault directory not found at: $VAULT_PATH"
    echo ""
    echo "Clone the private repo first (one time):"
    echo "    git clone https://github.com/teamolimpo/synapsis-vault.git \"$VAULT_PATH\""
    echo ""
    echo "Then re-run this command."
    exit 1
fi

# Basic sanity: does it look like the vault?
if [[ ! -d "$VAULT_PATH/Handoff" && ! -d "$VAULT_PATH/.git" ]]; then
    echo "WARNING: $VAULT_PATH does not look like a synapsis-vault (no Handoff/ or .git)."
    echo "Proceeding anyway..."
fi

# Safety: if Library exists and is NOT a symlink, refuse (prevents shadowing real dir)
if [[ -e "Library" && ! -L "Library" ]]; then
    echo "ERROR: 'Library' already exists and is a real file or directory (not a symlink)."
    echo "This would shadow the mount or pollute the public tree."
    echo ""
    echo "Fix with:"
    echo "    rm -rf Library"
    echo "Then re-run this script."
    exit 1
fi

# Create (or refresh) the external symlink
ln -sfn "$VAULT_PATH" Library
echo "✅ Symlink created: Library -> $VAULT_PATH (external)"

# Prepare the local hot runtime dir (gitignored)
mkdir -p .synapsis
echo "✅ .synapsis/ ready (local hot DB + config)"

echo ""
echo "You are now ready to go with your work tool."
echo "Next steps:"
echo "  - uv run python -m tools.synapsis stats     (or just 'synapsis stats' after install)"
echo "  - In Grok Build: try /handoff or /synapsis search"
echo "  - Optional: run the indexer for private knowledge:"
echo "      uv run python -m tools.knowledge_base.chunk_indexer update"
echo ""
echo "To verify:  bash scripts/vault-check.sh"
echo "To undo:    bash scripts/vault-unmount.sh"
