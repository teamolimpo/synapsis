#!/bin/bash
#
# vault-doctor.sh — more verbose diagnostic (for when things feel wrong)
#
set -euo pipefail

echo "=== synapsis vault doctor ==="
echo "Public root: $(pwd)"
echo ""

echo "1. Library entry:"
ls -ld Library 2>/dev/null || echo "   (does not exist)"

echo ""
echo "2. Symlink target:"
if [[ -L Library ]]; then
    readlink -f Library 2>/dev/null || readlink Library
else
    echo "   Not a symlink (or missing)"
fi

echo ""
echo "3. .synapsis/ (hot DB location):"
ls -ld .synapsis 2>/dev/null || echo "   (missing — will be created by mount)"

echo ""
echo "4. Vault contents (first level):"
if [[ -L Library ]]; then
    TARGET=$(readlink -f Library 2>/dev/null || readlink Library)
    ls -1 "$TARGET" 2>/dev/null | head -10 || echo "   (target unreadable)"
else
    echo "   (no symlink)"
fi

echo ""
echo "5. Recommendation:"
echo "   If anything is red, run:  bash scripts/vault-mount.sh"
echo "   Then verify with:         bash scripts/vault-check.sh"
echo ""
echo "   For the full Python CLI (once implemented): synapsis vault mount"
