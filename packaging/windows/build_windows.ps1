# =============================================================================
# hash-cli Windows installer builder (PowerShell)
#
# Produces: dist\hash-cli-<version>-windows-x86_64-setup.exe
#
# The installer bundles:
#   - hash-cli.exe (Python runtime + ALL pip dependencies via PyInstaller)
#   - PATH env var setup (so `hash-cli` works from any CMD/PowerShell)
#   - Optionally bundles the Ollama installer (place OllamaSetup.exe in packaging\windows\)
#
# Ollama + free models are installed on FIRST RUN (not bundled — 12GB+).
#
# Prerequisites (install once):
#   pip install pyinstaller
#   NSIS 3.x           -> https://nsis.sourceforge.io/Download
#   EnvVarUpdate plugin -> https://nsis.sourceforge.io/Environmental_Variables
#
# Usage (from repo root, PowerShell):
#   .\packaging\windows\build_windows.ps1
# =============================================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Get-Item "$PSScriptRoot\..\..").FullName
Set-Location $RepoRoot

$Version = python -c "import hash_cli; print(hash_cli.__version__)"
Write-Host "Building hash-cli $Version for Windows x86_64" -ForegroundColor Cyan

# ── 1. Clean ─────────────────────────────────────────────────────────────
if (Test-Path "build")             { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist\hash-cli.exe") { Remove-Item -Force "dist\hash-cli.exe" }

# ── 2. PyInstaller (bundles Python + all deps) ───────────────────────────
Write-Host "Running PyInstaller..." -ForegroundColor Cyan
python -m PyInstaller hash_cli.spec `
    --distpath "dist" `
    --workpath "build\windows" `
    --noconfirm

if (-not (Test-Path "dist\hash-cli.exe")) {
    Write-Host "PyInstaller did not produce dist\hash-cli.exe" -ForegroundColor Red
    exit 1
}
$SizeMB = [math]::Round((Get-Item 'dist\hash-cli.exe').Length / 1MB, 1)
Write-Host "  Binary size: $SizeMB MB"

# Ollama is NOT bundled — it's installed on first run of hash-cli.
# (Silent Ollama install would require admin; per-user first-run avoids that.)

# ── 4. NSIS installer ─────────────────────────────────────────────────────
$MakeNsis = Get-Command makensis -ErrorAction SilentlyContinue
if ($null -eq $MakeNsis) {
    Write-Host "makensis not found - skipping installer creation." -ForegroundColor Yellow
    Write-Host "  Install NSIS from https://nsis.sourceforge.io then re-run."
    Write-Host "  Standalone binary is at: dist\hash-cli.exe"
    exit 0
}

Write-Host "Building NSIS installer..." -ForegroundColor Cyan
& makensis "packaging\windows\installer.nsi"

$InstallerPath = "dist\hash-cli-$Version-windows-x86_64-setup.exe"
if (Test-Path $InstallerPath) {
    $ISize = [math]::Round((Get-Item $InstallerPath).Length / 1MB, 1)
    Write-Host ""
    Write-Host "Installer ready: $InstallerPath ($ISize MB)" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Install: double-click the .exe (or run silently with /S)"
    Write-Host "  Then open a NEW CMD/PowerShell and run: hash-cli"
    Write-Host "  (On first run hash-cli installs Ollama and pulls a model.)"
} else {
    Write-Host "Installer build failed." -ForegroundColor Red
    exit 1
}
