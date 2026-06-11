#!/bin/bash
#
# vault-unmount.sh (or reset)
#
# Safely removes the Library symlink so you can re-mount or switch vaults.
# Does NOT touch the actual vault content.
#
set -euo pipefail

if [[ -L "Library" ]]; then
    TARGET=$(readlink -f Library 2>/dev/null || readlink Library)
    echo "Removing symlink Library -> $TARGET"
    rm -f Library
    echo "✅ Symlink removed. The private vault at $TARGET is untouched."
elif [[ -e "Library" ]]; then
    echo "Library exists but is not a symlink:"
    ls -ld Library
    echo "Not touching it for safety. Remove manually if you really want."
    exit 1
else
    echo "Nothing to unmount (no Library entry)."
fi

echo ""
echo "You can now run vault-mount.sh again with a (different) target."
