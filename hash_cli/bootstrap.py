"""First-run bootstrap for hash-cli.

Ensures the runtime environment is ready:
  1. Ollama is installed (installs it if missing)
  2. At least one free model is pulled (offers to pull llama3.2:3b — smallest)

Runs automatically the first time hash-cli starts (tracked via a marker file).
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from hash_cli.config import CONFIG_DIR

_MARKER = CONFIG_DIR / ".bootstrapped"

# Free models offered on first run / via /pull, smallest first
_STARTER_MODELS = [
    ("llama3.2:3b",       "2 GB",  "fastest, good for quick tasks"),
    ("llama3.1:8b",       "5 GB",  "best all-round free model"),
    ("qwen2.5-coder:7b",  "5 GB",  "best for coding"),
    ("deepseek-r1:8b",    "5 GB",  "strong reasoning"),
    ("llama4:scout",      "~10 GB","Meta MoE, multimodal (needs 16GB+ RAM)"),
]


def is_first_run() -> bool:
    return not _MARKER.exists()


def mark_bootstrapped() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _MARKER.write_text("done", encoding="utf-8")


def _ollama_path() -> str | None:
    """Find the ollama executable, even if PATH wasn't refreshed after install.

    Checks PATH first, then the known per-user/system install locations.
    """
    # 1. On PATH
    p = shutil.which("ollama")
    if p:
        return p

    # 2. Known install locations
    import os
    system = platform.system()
    candidates: list[Path] = []
    if system == "Windows":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        candidates += [
            Path(localappdata) / "Programs" / "Ollama" / "ollama.exe",
            Path("C:/Program Files/Ollama/ollama.exe"),
        ]
    elif system == "Darwin":
        candidates += [
            Path("/usr/local/bin/ollama"),
            Path("/opt/homebrew/bin/ollama"),
            Path("/Applications/Ollama.app/Contents/Resources/ollama"),
        ]
    else:  # Linux
        candidates += [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]

    for c in candidates:
        if c.exists():
            return str(c)
    return None


def ollama_installed() -> bool:
    return _ollama_path() is not None


# ---------------------------------------------------------------------------
# Ollama installation
# ---------------------------------------------------------------------------

def install_ollama(console) -> bool:
    """Install Ollama for the current platform. Returns True on success."""
    system = platform.system()
    console.print_info("Installing Ollama…")

    try:
        if system == "Darwin":
            # Prefer Homebrew if available, else download the official installer
            if shutil.which("brew"):
                subprocess.run(["brew", "install", "ollama"], check=True, timeout=600)
            else:
                console.print_warning(
                    "Homebrew not found. Please install Ollama manually from:\n"
                    "  https://ollama.com/download\n"
                    "Then restart hash-cli."
                )
                return False

        elif system == "Linux":
            # Official install script
            subprocess.run(
                "curl -fsSL https://ollama.com/install.sh | sh",
                shell=True, check=True, timeout=600,
            )

        elif system == "Windows":
            # Download and run Ollama's installer. Ollama's Windows installer
            # is per-user and does NOT require admin.
            import tempfile
            import urllib.request
            url = "https://ollama.com/download/OllamaSetup.exe"
            tmp = Path(tempfile.gettempdir()) / "OllamaSetup.exe"
            console.print_info("Downloading Ollama installer…")
            try:
                urllib.request.urlretrieve(url, tmp)
                # /SILENT (not /VERYSILENT) — per-user, no admin prompt
                subprocess.run([str(tmp), "/SILENT"], check=True, timeout=600)
            except Exception as e:
                console.print_warning(
                    f"Automatic install failed ({e}).\n"
                    "Please install Ollama from https://ollama.com/download then restart hash-cli."
                )
                return False
            return ollama_installed()

        return ollama_installed()

    except subprocess.CalledProcessError as e:
        console.print_error(f"Ollama installation failed: {e}")
        return False
    except Exception as e:
        console.print_error(f"Could not install Ollama: {e}")
        return False


# ---------------------------------------------------------------------------
# Model pulling
# ---------------------------------------------------------------------------

def pull_model(model: str, console) -> bool:
    """Pull an Ollama model, streaming progress to the console."""
    console.print_info(f"Downloading {model} — this may take several minutes…")
    ollama_exe = _ollama_path() or "ollama"
    try:
        # encoding="utf-8" + errors="replace" prevents the Windows cp1252
        # 'charmap' decode crash on Ollama's progress-bar bytes.
        proc = subprocess.Popen(
            [ollama_exe, "pull", model],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        # Ollama redraws progress on the same line using \r. Read in chunks
        # and only show the latest status line (avoids console spam + crashes).
        last_status = ""
        buf = ""
        assert proc.stdout is not None
        while True:
            ch = proc.stdout.read(1)
            if ch == "":
                if proc.poll() is not None:
                    break
                continue
            if ch in ("\r", "\n"):
                line = buf.strip()
                buf = ""
                if line and line != last_status:
                    last_status = line
                    console.print(f"  [hash.dim]{line}[/hash.dim]")
            else:
                buf += ch

        proc.wait()
        if proc.returncode == 0:
            console.print_success(f"{model} ready.")
            return True
        console.print_error(f"Failed to download {model} (exit {proc.returncode}).")
        return False
    except Exception as e:
        console.print_error(f"Error pulling {model}: {e}")
        return False


# ---------------------------------------------------------------------------
# Full bootstrap flow
# ---------------------------------------------------------------------------

def run_bootstrap(console) -> None:
    """Run the first-time setup flow."""
    console.print("")
    console.print("[hash.brand]  ── First-time setup ──[/hash.brand]\n")
    console.print(
        "[hash.dim]  hash-cli needs Ollama to run free local models.\n"
        "  (You can skip this and use only cloud models with an API key.)[/hash.dim]\n"
    )

    # 1. Ollama
    if not ollama_installed():
        console.print("[hash.dim]  Install Ollama now? (Y/n):[/hash.dim]")
        ans = console.prompt_raw("  › ").strip().lower()
        if ans != "n":
            if not install_ollama(console):
                console.print_warning(
                    "Skipping model download. Install Ollama later from ollama.com,\n"
                    "  or use a cloud model with:  /model"
                )
                mark_bootstrapped()
                return
        else:
            console.print_info("Skipped. Use cloud models with /model, or install Ollama later.")
            mark_bootstrapped()
            return
    else:
        console.print_success("Ollama is already installed.")

    # 2. Starter model
    console.print("\n[hash.dim]  Download a starter model?[/hash.dim]")
    for i, (name, size, desc) in enumerate(_STARTER_MODELS, 1):
        console.print(f"  [hash.accent]{i}[/hash.accent]  {name:<20} ({size})  — {desc}")
    console.print("  [hash.accent]s[/hash.accent]  skip (I'll use cloud models or pull later)")
    choice = console.prompt_raw("  › ").strip().lower()

    if choice.isdigit() and 1 <= int(choice) <= len(_STARTER_MODELS):
        model = _STARTER_MODELS[int(choice) - 1][0]
        # Ensure Ollama server is up before pulling
        from hash_cli.ollama_launcher import ensure_ollama_running
        ensure_ollama_running()
        if pull_model(model, console):
            from hash_cli.config import set_active_model
            set_active_model(f"ollama/{model}")
            console.print_success(f"Set {model} as your active model.")
    else:
        console.print_info("Skipped model download.")

    mark_bootstrapped()
    console.print("\n[hash.success]  Setup complete! You're ready to go.[/hash.success]\n")
