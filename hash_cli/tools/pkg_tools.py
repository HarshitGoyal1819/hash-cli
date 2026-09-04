"""Package manager tool for hash-cli.

Allows the agent to install, uninstall, and upgrade packages via
pip, npm, brew, apt, or yarn — whichever is available.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from langchain_core.tools import tool

_TIMEOUT = 120  # package installs can be slow


def _run(cmd: str, cwd: str = ".") -> str:
    """Run a shell command and return formatted output."""
    try:
        import platform as _plat
        _exe = None
        if _plat.system() != "Windows" and Path("/bin/zsh").exists():
            _exe = "/bin/zsh"

        result = subprocess.run(
            cmd,
            shell=True,
            executable=_exe,
            cwd=str(Path(cwd).expanduser().resolve()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
        )
        parts = []
        if result.stdout.strip():
            parts.append(result.stdout.rstrip())
        if result.stderr.strip():
            parts.append(f"[stderr]\n{result.stderr.rstrip()}")
        output = "\n".join(parts) if parts else "(no output)"
        return output + f"\n[exit code: {result.returncode}]"
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {_TIMEOUT}s"
    except Exception as exc:
        return f"Error: {exc}"


def _which(cmd: str) -> bool:
    return shutil.which(cmd) is not None


@tool
def manage_packages(
    action: str,
    packages: list,
    manager: str = "auto",
    cwd: str = ".",
) -> str:
    """Install, uninstall, or upgrade packages using pip, npm, brew, apt, or yarn.

    Use this when the user asks to install, remove, or upgrade any package,
    library, or dependency.

    Args:
        action:   One of "install", "uninstall", "upgrade", "list".
        packages: List of package names, e.g. ["requests", "fastapi"].
                  Can be empty for "list".
        manager:  Package manager to use: "pip", "npm", "brew", "apt", "yarn",
                  or "auto" (auto-detect from context). Default "auto".
        cwd:      Working directory (relevant for npm/yarn). Default ".".

    Returns:
        Command output showing what was installed/removed, or an error.

    Examples:
        manage_packages("install", ["requests", "pandas"], "pip")
        manage_packages("uninstall", ["lodash"], "npm")
        manage_packages("upgrade", ["pip"], "pip")
        manage_packages("list", [], "pip")
    """
    action = action.lower().strip()
    if action not in ("install", "uninstall", "upgrade", "list"):
        return f"Error: action must be install, uninstall, upgrade, or list. Got: {action}"

    # ── Auto-detect manager ───────────────────────────────────────────
    if manager == "auto":
        # Guess from package names or environment
        pkg_str = " ".join(packages).lower()
        if any(p in pkg_str for p in ("react", "vue", "express", "typescript", "webpack")):
            manager = "npm"
        elif Path(cwd).expanduser().resolve().joinpath("package.json").exists():
            manager = "npm"
        else:
            manager = "pip"

    # ── Build command ─────────────────────────────────────────────────
    pkg_list = " ".join(f'"{p}"' for p in packages) if packages else ""

    if manager == "pip":
        if not _which("pip") and not _which("pip3"):
            return "Error: pip not found. Install Python first."
        pip = "pip3" if _which("pip3") else "pip"
        cmds = {
            "install":   f"{pip} install {pkg_list}",
            "uninstall": f"{pip} uninstall -y {pkg_list}",
            "upgrade":   f"{pip} install --upgrade {pkg_list}",
            "list":      f"{pip} list",
        }

    elif manager == "npm":
        if not _which("npm"):
            return "Error: npm not found. Install Node.js first."
        cmds = {
            "install":   f"npm install {pkg_list}",
            "uninstall": f"npm uninstall {pkg_list}",
            "upgrade":   f"npm update {pkg_list}",
            "list":      "npm list --depth=0",
        }

    elif manager == "yarn":
        if not _which("yarn"):
            return "Error: yarn not found. Run: npm install -g yarn"
        cmds = {
            "install":   f"yarn add {pkg_list}",
            "uninstall": f"yarn remove {pkg_list}",
            "upgrade":   f"yarn upgrade {pkg_list}",
            "list":      "yarn list --depth=0",
        }

    elif manager == "brew":
        if not _which("brew"):
            return "Error: Homebrew not found. Install from https://brew.sh"
        cmds = {
            "install":   f"brew install {pkg_list}",
            "uninstall": f"brew uninstall {pkg_list}",
            "upgrade":   f"brew upgrade {pkg_list}" if pkg_list else "brew upgrade",
            "list":      "brew list",
        }

    elif manager == "apt":
        if not _which("apt"):
            return "Error: apt not found (not a Debian/Ubuntu system)."
        cmds = {
            "install":   f"sudo apt-get install -y {pkg_list}",
            "uninstall": f"sudo apt-get remove -y {pkg_list}",
            "upgrade":   f"sudo apt-get upgrade -y {pkg_list}" if pkg_list else "sudo apt-get upgrade -y",
            "list":      "apt list --installed",
        }

    else:
        return f"Error: Unknown manager '{manager}'. Use: pip, npm, yarn, brew, apt, or auto."

    cmd = cmds[action]
    label = f"[{manager}] {action}" + (f" {', '.join(packages)}" if packages else "")
    result = _run(cmd, cwd=cwd)
    return f"{label}\n$ {cmd}\n\n{result}"
