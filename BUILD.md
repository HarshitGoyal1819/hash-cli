# Building hash-cli Installers

This guide produces self-contained installers for **macOS** and **Windows**.

## What gets bundled

| Component | Bundled in installer? | How it's installed |
|---|---|---|
| hash-cli app | ✅ Yes | PyInstaller binary |
| Python runtime | ✅ Yes | Embedded by PyInstaller |
| All pip packages (langchain, langgraph, providers, mcp, rich, prompt_toolkit, openpyxl, reportlab, etc.) | ✅ Yes | Embedded by PyInstaller |
| PATH setup | ✅ Yes | postinstall (mac) / NSIS (win) |
| Admin/sudo password | ❌ Never | Per-user install: `~/.local/bin` (mac), `%LOCALAPPDATA%\Programs\hash-cli` (win) |
| Ollama | ⚙️ First run | Auto-installed on first launch (or bundled on Windows if you add `OllamaSetup.exe`) |
| Free models (llama3.1, llama3.2, qwen2.5-coder) | ⚙️ First run | Downloaded on first launch with progress |

**Why models aren't bundled:** the three free models total ~12GB. Bundling them
would make the installer 12GB+. Instead, the first time a user runs `hash-cli`,
it offers to install Ollama and pull a starter model — the standard approach
used by Ollama, Docker Desktop, etc.

The user does **not** need Python, pip, or any dependencies pre-installed.
Everything the app needs is inside the binary.

---

## Prerequisites (build machine)

Install PyInstaller in your project venv:

```bash
source .venv/bin/activate
pip install pyinstaller
```

---

## macOS — build the `.pkg`

Run on a Mac (Apple Silicon can build both slices; Intel builds x86_64 only):

```bash
bash packaging/macos/build_mac.sh
```

Produces:
```
dist/hash-cli-0.1.0-macos-universal.pkg
```

This is a **universal binary** — runs natively on both Intel and Apple Silicon.

**Install it (NO admin password needed):**

Double-click the `.pkg` in Finder — it installs to your home folder (`~/.local/bin`), so macOS won't ask for an admin password.

Or from the command line:
```bash
installer -pkg dist/hash-cli-0.1.0-macos-universal.pkg -target CurrentUserHomeDirectory
```

Then open a **new** terminal:
```bash
hash-cli
```

On first run it walks the user through installing Ollama and pulling a model.

### Code signing (optional, for distribution)

To distribute without Gatekeeper warnings, sign and notarize:

```bash
# Sign the binary
codesign --force --options runtime --sign "Developer ID Application: Your Name (TEAMID)" \
    dist/pkg_root/usr/local/bin/hash-cli

# Sign the pkg
productsign --sign "Developer ID Installer: Your Name (TEAMID)" \
    dist/hash-cli-0.1.0-macos-universal.pkg \
    dist/hash-cli-0.1.0-macos-universal-signed.pkg

# Notarize
xcrun notarytool submit dist/hash-cli-0.1.0-macos-universal-signed.pkg \
    --apple-id you@example.com --team-id TEAMID --wait
```

---

## Windows — build the `.exe`

Run on a Windows machine.

**1. Install prerequisites:**
- `pip install pyinstaller`
- [NSIS 3.x](https://nsis.sourceforge.io/Download)
- [EnvVarUpdate NSIS plugin](https://nsis.sourceforge.io/Environmental_Variables)

**2. (Optional) Bundle the Ollama installer** so it installs silently:
- Download `OllamaSetup.exe` from https://ollama.com/download/windows
- Place it at `packaging\windows\OllamaSetup.exe`

**3. Build:**
```powershell
.\packaging\windows\build_windows.ps1
```

Produces:
```
dist\hash-cli-0.1.0-windows-x86_64-setup.exe
```

**Install it:**
- Double-click the `.exe`, or silently: `.\hash-cli-0.1.0-windows-x86_64-setup.exe /S`

Then open a **new** CMD or PowerShell:
```
hash-cli
```

### Code signing (optional)

```powershell
signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 `
    dist\hash-cli-0.1.0-windows-x86_64-setup.exe
```

---

## Testing the build without installing

You can run the PyInstaller binary directly from `dist/` before packaging:

```bash
# macOS
./dist/arm64/hash-cli          # or dist/x86_64/hash-cli

# Windows
.\dist\hash-cli.exe
```

---

## Troubleshooting builds

**`ModuleNotFoundError` at runtime after building**
A dynamic import wasn't collected. Add the package to the `collect_all` loop in
`hash_cli.spec`, or add the specific module to `hiddenimports`.

**Binary is very large (200MB+)**
That's expected — it embeds Python + langchain + all providers. UPX compression
is disabled because it breaks macOS signing. This is normal for a bundled agent.

**macOS: "cannot be opened because the developer cannot be verified"**
The binary isn't signed/notarized. Either sign it (see above) or the user can
right-click → Open, or run `xattr -d com.apple.quarantine /usr/local/bin/hash-cli`.

**Windows: SmartScreen warning**
Unsigned installer. Sign it (see above) or users click "More info → Run anyway".
