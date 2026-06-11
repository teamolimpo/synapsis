#!/bin/bash
#
# vault-check.sh — quick verification that the external vault is mounted
#
set -euo pipefail

echo "==> synapsis vault mount check"

if [[ ! -e "Library" ]]; then
    echo "❌ Library does not exist"
    echo "   Run: bash scripts/vault-mount.sh"
    exit 1
fi

if [[ ! -L "Library" ]]; then
    echo "❌ Library exists but is NOT a symlink"
    ls -ld Library
    echo "   Clean it (rm -rf Library) and re-mount."
    exit 1
fi

TARGET=$(readlink -f Library || readlink Library)
echo "✅ Library is a symlink → $TARGET"

if [[ ! -d "$TARGET" ]]; then
    echo "❌ Target does not exist or is not a directory"
    exit 1
fi

if [[ -d "$TARGET/Handoff" || -d "$TARGET/.git" ]]; then
    echo "✅ Target looks like a valid synapsis-vault"
else
    echo "⚠️  Target exists but may not be the expected vault (no Handoff/ or .git)"
fi

if [[ -d ".synapsis" ]]; then
    echo "✅ .synapsis/ (hot runtime) present"
else
    echo "⚠️  .synapsis/ missing (run mount again)"
fi

echo ""
echo "All basic checks passed. You should be ready for handoffs and private memory."
