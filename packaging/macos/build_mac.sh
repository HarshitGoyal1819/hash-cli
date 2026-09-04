#!/usr/bin/env bash
# =============================================================================
# hash-cli macOS installer builder
#
# Produces: dist/hash-cli-<version>-macos-universal.pkg
#
# The .pkg bundles:
#   - hash-cli binary (Python runtime + ALL pip dependencies via PyInstaller)
#   - PATH setup (so `hash-cli` works from any terminal)
#   - postinstall runs first-run bootstrap prompts
#
# Ollama + free models are installed on FIRST RUN (not bundled — they're 12GB+).
#
# Prerequisites (install once in your build venv):
#   pip install pyinstaller
#
# Usage (from repo root):
#   bash packaging/macos/build_mac.sh
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

VERSION=$(python3 -c "import hash_cli; print(hash_cli.__version__)")
APP_NAME="hash-cli"
DIST="$REPO_ROOT/dist"
PKG_ROOT="$DIST/pkg_root"
PKG_SCRIPTS="$DIST/pkg_scripts"
# Per-user install path (relative to the user's home) — no admin needed.
# The pkg install-location is the user's home; binary goes to ~/.local/bin.
INSTALL_DIR=".local/bin"
IDENTIFIER="com.hashcli.hash-cli"

echo "▶  Building hash-cli $VERSION for macOS (universal)"
echo ""

# ── 1. Clean ────────────────────────────────────────────────────────────
rm -rf build "$DIST/x86_64" "$DIST/arm64" "$PKG_ROOT" "$PKG_SCRIPTS"
mkdir -p "$PKG_ROOT/$INSTALL_DIR" "$PKG_SCRIPTS"

# ── 2. Build both architecture slices ────────────────────────────────────
echo "▶  Building x86_64 slice…"
python3 -m PyInstaller hash_cli.spec \
    --distpath "$DIST/x86_64" --workpath "build/x86_64" \
    --noconfirm --target-arch x86_64

echo "▶  Building arm64 slice…"
python3 -m PyInstaller hash_cli.spec \
    --distpath "$DIST/arm64" --workpath "build/arm64" \
    --noconfirm --target-arch arm64

# ── 3. Merge into a universal binary ──────────────────────────────────────
echo "▶  Merging into universal binary with lipo…"
lipo -create \
    "$DIST/x86_64/hash-cli" \
    "$DIST/arm64/hash-cli" \
    -output "$PKG_ROOT/$INSTALL_DIR/hash-cli"
chmod +x "$PKG_ROOT/$INSTALL_DIR/hash-cli"

echo "   $(file "$PKG_ROOT/$INSTALL_DIR/hash-cli")"

# ── 4. Postinstall script (PATH + welcome) ────────────────────────────────
cp "$REPO_ROOT/packaging/macos/scripts/postinstall" "$PKG_SCRIPTS/postinstall"
chmod +x "$PKG_SCRIPTS/postinstall"

# ── 5. Build component pkg ────────────────────────────────────────────────
COMPONENT_PKG="$DIST/hash-cli-component.pkg"
echo "▶  Building component package…"
# install-location is the user's home (~) — pkg contents go under it.
pkgbuild \
    --root       "$PKG_ROOT" \
    --scripts    "$PKG_SCRIPTS" \
    --identifier "$IDENTIFIER" \
    --version    "$VERSION" \
    --install-location "$HOME" \
    "$COMPONENT_PKG"

# ── 6. Wrap in a distribution product pkg (with welcome/license screens) ───
FINAL_PKG="$DIST/${APP_NAME}-${VERSION}-macos-universal.pkg"
RES="$REPO_ROOT/packaging/macos/resources"

if [ -f "$RES/distribution.xml" ]; then
    echo "▶  Building distribution installer…"
    productbuild \
        --distribution "$RES/distribution.xml" \
        --resources    "$RES" \
        --package-path "$DIST" \
        "$FINAL_PKG"
else
    cp "$COMPONENT_PKG" "$FINAL_PKG"
fi

rm -f "$COMPONENT_PKG"

echo ""
echo "✓  Installer ready:  $FINAL_PKG"
echo ""
echo "   Install (NO admin password needed — double-click the pkg, or):"
echo "     installer -pkg \"$FINAL_PKG\" -target CurrentUserHomeDirectory"
echo "   Then open a NEW terminal and run:"
echo "     hash-cli"
echo ""
echo "   (On first run, hash-cli will offer to install Ollama and pull a model.)"
