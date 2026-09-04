# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for hash-cli — bundles Python + all dependencies.
#
# Build on macOS  → dist/hash-cli   (then wrapped into .pkg)
# Build on Windows → dist/hash-cli.exe (then wrapped into NSIS installer)

import os
import platform
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH)
PKG  = ROOT / "hash_cli"

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"

# ── Collect everything for the heavy dynamic-import packages ───────────────
datas = []
binaries = []
hiddenimports = []

for pkg in (
    "langchain",
    "langchain_core",
    "langchain_ollama",
    "langchain_openai",
    "langchain_anthropic",
    "langchain_google_genai",
    "langgraph",
    "openai",
    "anthropic",
    "google.generativeai" if False else "google",
    "tiktoken",
    "tiktoken_ext",
    "typer",
    "click",
    "rich",
    "prompt_toolkit",
    "openpyxl",
    "reportlab",
    "yaml",
    "httpx",
    "httpcore",
    "bs4",
    "ddgs",
    "pydantic",
    "pydantic_core",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# Explicit hidden imports for our own dynamic references + stragglers
hiddenimports += collect_submodules("hash_cli")
hiddenimports += [
    "tiktoken_ext.openai_public",
    "urllib.request", "urllib.error",
    "json", "csv", "io", "re", "subprocess", "platform", "getpass",
]

# Include the whole package source so any runtime path resolution works
datas += [(str(PKG), "hash_cli")]


a = Analysis(
    [str(PKG / "cli.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "common" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL", "scipy", "numpy", "pandas", "IPython", "jupyter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="hash-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=not IS_WIN,
    upx=False,          # UPX can break signed binaries on macOS — disabled
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # CLI — must stay True
    disable_windowed_traceback=False,
    argv_emulation=False,
    # target_arch is read from the HASHCLI_TARGET_ARCH env var so build scripts
    # can select the arch WITHOUT passing --target-arch (which is illegal with a spec).
    #   unset / "native"    → current arch
    #   "universal2"        → universal (needs a universal2 Python)
    #   "x86_64" / "arm64"  → specific slice
    target_arch=(
        None if os.environ.get("HASHCLI_TARGET_ARCH", "native") in ("", "native")
        else os.environ["HASHCLI_TARGET_ARCH"]
    ),
    codesign_identity=None,
    entitlements_file=None,
    icon=(
        str(ROOT / "packaging" / "macos" / "resources" / "hash-cli.icns")
        if IS_MAC and (ROOT / "packaging" / "macos" / "resources" / "hash-cli.icns").exists()
        else (
            str(ROOT / "packaging" / "windows" / "hash-cli.ico")
            if IS_WIN and (ROOT / "packaging" / "windows" / "hash-cli.ico").exists()
            else None
        )
    ),
)
