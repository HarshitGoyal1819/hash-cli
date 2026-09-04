"""Ollama auto-launcher for hash-cli.

Checks if Ollama is already running on localhost:11434.
If not, opens a NEW terminal window/tab and starts `ollama serve` in it.
On shutdown, kills the process AND closes the terminal window we opened.

Platform support:
  macOS  — AppleScript via osascript (Terminal.app or iTerm2)
  Windows — start cmd.exe in a new window
  Linux   — tries gnome-terminal, xterm, konsole in order
"""

from __future__ import annotations

import platform
import subprocess
import time
import urllib.error
import urllib.request

_OLLAMA_URL = "http://localhost:11434"
_STARTUP_WAIT = 12  # seconds — Ollama can be slow to start, especially first time

# Track whether WE opened the terminal (so we only close what we opened)
_opened_terminal: bool = False
_opened_via: str = ""   # "terminal" | "iterm2" | "background"


def is_ollama_running() -> bool:
    """Return True if Ollama is already serving on localhost:11434."""
    try:
        urllib.request.urlopen(f"{_OLLAMA_URL}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def ensure_ollama_running() -> bool:
    """Ensure Ollama is running.

    If already running → returns True immediately (no new terminal opened).
    If not running    → opens a new terminal window with `ollama serve`,
                        waits up to _STARTUP_WAIT seconds, then returns True.
    Returns False if we could not launch it.
    """
    global _opened_terminal, _opened_via

    if is_ollama_running():
        return True

    system = platform.system()
    launched = False

    if system == "Darwin":
        launched = _launch_macos()
    elif system == "Windows":
        launched = _launch_windows()
    else:
        launched = _launch_linux()

    if launched:
        _opened_terminal = True

    if not launched:
        return False

    deadline = time.time() + _STARTUP_WAIT
    while time.time() < deadline:
        if is_ollama_running():
            return True
        time.sleep(0.5)

    return is_ollama_running()


# ---------------------------------------------------------------------------
# Platform launchers
# ---------------------------------------------------------------------------

def _launch_macos() -> bool:
    """Open a new Terminal.app window running `ollama serve`."""
    global _opened_via

    iterm_check = subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to return exists process "iTerm2"'],
        capture_output=True, text=True, timeout=5,
    )
    use_iterm = iterm_check.returncode == 0 and "true" in iterm_check.stdout.lower()

    if use_iterm:
        _opened_via = "iterm2"
        script = (
            'tell application "iTerm2"\n'
            '    create window with default profile\n'
            '    tell current session of current window\n'
            '        write text "ollama serve; exit"\n'
            '    end tell\n'
            'end tell'
        )
    else:
        _opened_via = "terminal"
        script = (
            'tell application "Terminal"\n'
            '    do script "ollama serve; exit"\n'
            '    activate\n'
            'end tell'
        )

    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        _opened_via = "background"
        return _launch_background()


def _launch_windows() -> bool:
    """Open a new cmd.exe window running `ollama serve`."""
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "cmd.exe", "/k", "ollama serve"],
            shell=False,
            creationflags=subprocess.CREATE_NEW_CONSOLE,  # type: ignore[attr-defined]
        )
        return True
    except Exception:
        return _launch_background()


def _launch_linux() -> bool:
    """Try common terminal emulators on Linux."""
    terminals = [
        ["gnome-terminal", "--", "bash", "-c", "ollama serve; exec bash"],
        ["xterm", "-e", "bash -c 'ollama serve; exec bash'"],
        ["konsole", "-e", "bash -c 'ollama serve; exec bash'"],
        ["xfce4-terminal", "-e", "bash -c 'ollama serve; exec bash'"],
    ]
    for cmd in terminals:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except FileNotFoundError:
            continue
    return _launch_background()


def _launch_background() -> bool:
    """Fallback: launch ollama serve as a detached background process."""
    try:
        kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if platform.system() == "Windows":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True

        subprocess.Popen(["ollama", "serve"], **kwargs)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

def stop_ollama() -> bool:
    """Stop the Ollama server and close the terminal window we opened.

    Returns True if Ollama was stopped (or was not running).
    """
    if not is_ollama_running():
        _close_ollama_terminal()
        return True

    # 1. Try the Ollama REST shutdown endpoint (Ollama ≥ 0.1.38)
    try:
        req = urllib.request.Request(
            f"{_OLLAMA_URL}/api/shutdown", method="POST", data=b""
        )
        urllib.request.urlopen(req, timeout=3)
        time.sleep(1)
    except Exception:
        pass

    # 2. Kill by process name if still running
    if is_ollama_running():
        system = platform.system()
        try:
            if system == "Windows":
                subprocess.run(
                    ["taskkill", "/F", "/IM", "ollama.exe"],
                    capture_output=True, timeout=5,
                )
            else:
                subprocess.run(
                    ["pkill", "-SIGTERM", "ollama"],
                    capture_output=True, timeout=5,
                )
            time.sleep(1)
        except Exception:
            pass

    # 3. Close the terminal window we opened
    _close_ollama_terminal()

    # 4. Small delay then check if window still open — force close if needed
    if platform.system() == "Darwin" and _opened_via in ("terminal", "iterm2"):
        time.sleep(0.5)  # let 'exit' command run naturally first

    return not is_ollama_running()


def _close_ollama_terminal() -> None:
    """Close the terminal window that was running ollama serve.

    Because we launch with 'ollama serve; exit', the shell exits automatically
    when ollama is killed — and most terminal emulators close the window when
    the shell exits. This function is a backup in case that doesn't happen.
    """
    global _opened_terminal, _opened_via

    if not _opened_terminal:
        return

    system = platform.system()

    if system == "Darwin":
        if _opened_via == "iterm2":
            script = (
                'tell application "iTerm2"\n'
                '    repeat with w in windows\n'
                '        repeat with t in tabs of w\n'
                '            repeat with s in sessions of t\n'
                '                if (name of s) contains "ollama" then\n'
                '                    close w\n'
                '                    return\n'
                '                end if\n'
                '            end repeat\n'
                '        end repeat\n'
                '    end repeat\n'
                'end tell'
            )
        else:
            # Terminal.app closes window automatically when shell exits cleanly
            # (Shell > When shell exits: Close if shell exited cleanly)
            # Backup: find by "ollama" in window name
            script = (
                'tell application "Terminal"\n'
                '    repeat with w in windows\n'
                '        try\n'
                '            if name of w contains "ollama" then\n'
                '                close w\n'
                '                return\n'
                '            end if\n'
                '        end try\n'
                '    end repeat\n'
                'end tell'
            )
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        except Exception:
            pass

    elif system == "Windows":
        try:
            subprocess.run(
                ["taskkill", "/F", "/FI", "WINDOWTITLE eq ollama*"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

    _opened_terminal = False
    _opened_via = ""
