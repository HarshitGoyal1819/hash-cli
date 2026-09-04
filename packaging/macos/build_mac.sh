#!/usr/bin/env bash
# =============================================================================
# hash-cli macOS installer builder — PER-USER, NO ADMIN.
#
# Produces: dist/hash-cli-<version>-macos-<arch>.pkg
#
# Arch handling (PyInstaller reads HASHCLI_TARGET_ARCH from env — never pass
# --target-arch on the CLI, that is illegal when a .spec file is used):
#   - If the Python is universal2  → builds a UNIVERSAL binary (Intel + Apple Silicon)
#   - Otherwise                     → builds a native single-arch binary
#
# The .pkg installs to ~/.local/bin (home folder) so NO admin password is asked.
# Ollama + models are installed on first run.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

VERSION=$(python3 -c "import hash_cli; print(hash_cli.__version__)")
APP_NAME="hash-cli"
DIST="$REPO_ROOT/dist"
PKG_ROOT="$DIST/pkg_root"
PKG_SCRIPTS="$DIST/pkg_scripts"
INSTALL_DIR=".local/bin"
IDENTIFIER="com.hashcli.hash-cli"

# ── Detect whether the Python can build universal2 ────────────────────────
PY_ARCHS=$(python3 -c "import subprocess,sys; print(subprocess.run(['lipo','-archs',sys.executable],capture_output=True,text=True).stdout.strip())" 2>/dev/null || echo "")
if echo "$PY_ARCHS" | grep -q "x86_64" && echo "$PY_ARCHS" | grep -q "arm64"; then
    export HASHCLI_TARGET_ARCH="universal2"
    ARCH_LABEL="universal"
    echo "▶  Universal2 Python detected — building UNIVERSAL binary."
else
    export HASHCLI_TARGET_ARCH="native"
    ARCH_LABEL="$(uname -m)"
    echo "▶  Single-arch Python ($PY_ARCHS) — building native ($ARCH_LABEL) binary."
fi

echo "▶  Building hash-cli $VERSION for macOS ($ARCH_LABEL)"
echo ""

# ── 1. Clean ────────────────────────────────────────────────────────────
rm -rf build "$DIST/onefile" "$PKG_ROOT" "$PKG_SCRIPTS"
mkdir -p "$PKG_ROOT/$INSTALL_DIR" "$PKG_SCRIPTS"

# ── 2. Build the binary (arch chosen via HASHCLI_TARGET_ARCH env) ─────────
python3 -m PyInstaller hash_cli.spec --distpath "$DIST/onefile" --workpath "build" --noconfirm

cp "$DIST/onefile/hash-cli" "$PKG_ROOT/$INSTALL_DIR/hash-cli"
chmod +x "$PKG_ROOT/$INSTALL_DIR/hash-cli"
echo "   $(file "$PKG_ROOT/$INSTALL_DIR/hash-cli")"

# ── 3. Postinstall (PATH setup, runs as the user) ─────────────────────────
cp "$REPO_ROOT/packaging/macos/scripts/postinstall" "$PKG_SCRIPTS/postinstall"
chmod +x "$PKG_SCRIPTS/postinstall"

# ── 4. Component pkg (installs into the user's home) ──────────────────────
echo "▶  Building component package…"
pkgbuild \
    --root       "$PKG_ROOT" \
    --scripts    "$PKG_SCRIPTS" \
    --identifier "$IDENTIFIER" \
    --version    "$VERSION" \
    --install-location "$HOME" \
    "$DIST/hash-cli.pkg"

# ── 5. Distribution product pkg (welcome/license, per-user domain) ────────
FINAL_PKG="$DIST/${APP_NAME}-${VERSION}-macos-${ARCH_LABEL}.pkg"
RES="$REPO_ROOT/packaging/macos/resources"

if [ -f "$RES/distribution.xml" ]; then
    echo "▶  Building distribution installer…"
    productbuild \
        --distribution "$RES/distribution.xml" \
        --resources    "$RES" \
        --package-path "$DIST" \
        "$FINAL_PKG"
else
    cp "$DIST/hash-cli.pkg" "$FINAL_PKG"
fi

rm -f "$DIST/hash-cli.pkg"

echo ""
echo "✓  Installer ready:  $FINAL_PKG"
echo "   Install (NO admin needed): double-click the pkg, or:"
echo "     installer -pkg \"$FINAL_PKG\" -target CurrentUserHomeDirectory"
