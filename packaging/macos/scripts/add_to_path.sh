#!/usr/bin/env bash
# =============================================================================
# add_to_path.sh — add /usr/local/bin to PATH for hash-cli
# Run this if the postinstall script didn't fire (e.g. manual binary copy).
#
#   Usage:  bash packaging/macos/scripts/add_to_path.sh
# =============================================================================

set -euo pipefail

INSTALL_PATH="/usr/local/bin/hash-cli"
PATH_LINE='export PATH="/usr/local/bin:$PATH"'
COMMENT='# Added by hash-cli'

# Detect shell rc file
case "${SHELL:-}" in
    */zsh)  RC="$HOME/.zshrc" ;;
    */fish)
        FISH="$HOME/.config/fish/config.fish"
        mkdir -p "$(dirname "$FISH")"
        grep -q "hash-cli" "$FISH" 2>/dev/null || {
            echo "" >> "$FISH"
            echo "$COMMENT" >> "$FISH"
            echo "fish_add_path /usr/local/bin" >> "$FISH"
        }
        echo "✓  PATH updated in $FISH — restart your terminal."
        exit 0
        ;;
    *)      RC="$HOME/.bash_profile" ;;
esac

if ! grep -q '/usr/local/bin' "$RC" 2>/dev/null; then
    { echo ""; echo "$COMMENT"; echo "$PATH_LINE"; } >> "$RC"
    echo "✓  Added /usr/local/bin to PATH in $RC"
else
    echo "ℹ  /usr/local/bin already in $RC"
fi

# System-wide (so new Terminal windows see it without sourcing rc)
if [ -d "/etc/paths.d" ] && [ ! -f "/etc/paths.d/hash-cli" ]; then
    echo "/usr/local/bin" | sudo tee /etc/paths.d/hash-cli > /dev/null 2>&1 || true
fi

echo ""
echo "✓  Done. Restart your terminal, then run:  hash-cli"
