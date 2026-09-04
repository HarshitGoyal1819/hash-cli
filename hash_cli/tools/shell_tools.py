"""Shell execution tool for the hash-cli agent."""

from __future__ import annotations

import subprocess
from pathlib import Path

from langchain_core.tools import tool

_BLOCKED = {
    "rm -rf /", "rm -rf ~", "mkfs", "dd if=/dev/zero", ":(){:|:&};:",
}
_TIMEOUT = 60


@tool
def run_command(command: str, cwd: str = ".") -> str:
    """Run a shell command and return its output.

    Use this for installing packages, running tests, compiling, git operations,
    or any other terminal task.

    Args:
        command: Shell command to execute.
        cwd:     Working directory for the command. Defaults to current directory.

    Returns:
        Combined stdout and stderr output plus exit code.
    """
    cmd_lower = command.lower()
    for blocked in _BLOCKED:
        if blocked in cmd_lower:
            return f"Error: Blocked potentially destructive command: {command}"

    working_dir = Path(cwd).expanduser().resolve()
    if not working_dir.exists():
        return f"Error: Working directory not found: {cwd}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            executable="/bin/zsh" if Path("/bin/zsh").exists() else None,
            cwd=str(working_dir),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
        parts: list[str] = []
        if result.stdout.strip():
            parts.append(result.stdout.rstrip())
        if result.stderr.strip():
            parts.append(f"[stderr]\n{result.stderr.rstrip()}")
        output = "\n".join(parts) if parts else "(no output)"
        return output + f"\n[exit code: {result.returncode}]"
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {_TIMEOUT} seconds."
    except Exception as exc:
        return f"Error running command: {exc}"
