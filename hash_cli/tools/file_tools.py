"""File system tools for the hash-cli agent."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool


def _resolve(path: str) -> Path:
    return Path(path).expanduser().resolve()


@tool
def read_file(path: str, offset: int = 0, limit: int = 200) -> str:
    """Read a file from the filesystem.

    Args:
        path:   Path to the file (absolute or relative to current directory).
        offset: Line number to start reading from (0-indexed). Default 0.
        limit:  Maximum number of lines to return. Default 200.

    Returns:
        File contents as a string with line numbers, or an error message.
    """
    try:
        target = _resolve(path)
        if not target.exists():
            return f"Error: File not found: {path}"
        if not target.is_file():
            return f"Error: Path is not a file: {path}"
        if target.stat().st_size > 2 * 1024 * 1024:
            return "Error: File is too large (> 2 MB). Use search_files to locate specific content."

        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        chunk = lines[offset : offset + limit]
        numbered = "\n".join(f"{offset + i + 1:>6} | {line}" for i, line in enumerate(chunk))
        footer = ""
        if offset + limit < total:
            footer = f"\n... ({total - offset - limit} more lines — use offset/limit to read further)"
        return f"File: {path}  [{total} lines total]\n{numbered}{footer}"
    except PermissionError:
        return f"Error: Permission denied reading {path}"
    except Exception as exc:
        return f"Error reading {path}: {exc}"


@tool
def write_file(path: str, content: str) -> str:
    """Create or overwrite a file with the given content.

    Creates parent directories automatically.

    Args:
        path:    Path to the file to write.
        content: Full text content to write.

    Returns:
        Success message with number of bytes written, or an error message.
    """
    try:
        target = _resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"✓ Wrote {len(content.encode())} bytes to {path}"
    except PermissionError:
        return f"Error: Permission denied writing to {path}"
    except Exception as exc:
        return f"Error writing {path}: {exc}"


@tool
def edit_file(path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
    """Replace a specific string inside an existing file.

    The old_str must match exactly (including whitespace and indentation).

    Args:
        path:        Path to the file to edit.
        old_str:     Exact text to find.
        new_str:     Replacement text.
        replace_all: If True, replace every occurrence. Default False.

    Returns:
        Success message showing how many replacements were made, or an error message.
    """
    try:
        target = _resolve(path)
        if not target.exists():
            return f"Error: File not found: {path}"

        original = target.read_text(encoding="utf-8", errors="replace")
        count = original.count(old_str)
        if count == 0:
            return (
                f"Error: old_str not found in {path}.\n"
                "Make sure whitespace and indentation match exactly."
            )
        if count > 1 and not replace_all:
            return (
                f"Error: old_str appears {count} times in {path}. "
                "Add more surrounding context to make it unique, or set replace_all=True."
            )

        new_content = original.replace(old_str, new_str) if replace_all else original.replace(old_str, new_str, 1)
        replacements = count if replace_all else 1
        target.write_text(new_content, encoding="utf-8")
        return f"✓ Made {replacements} replacement(s) in {path}"
    except PermissionError:
        return f"Error: Permission denied editing {path}"
    except Exception as exc:
        return f"Error editing {path}: {exc}"


@tool
def delete_file(path: str) -> str:
    """Permanently delete a file from the filesystem.

    Args:
        path: Path to the file to delete.

    Returns:
        Success message, or an error message if the file doesn't exist.
    """
    try:
        target = _resolve(path)
        if not target.exists():
            return f"Error: File not found: {path}"
        if not target.is_file():
            return f"Error: Path is not a file (use run_command with 'rm -rf' for directories): {path}"
        target.unlink()
        return f"✓ Deleted: {path}"
    except PermissionError:
        return f"Error: Permission denied deleting {path}"
    except Exception as exc:
        return f"Error deleting {path}: {exc}"


_IGNORED = {".git", "__pycache__", ".mypy_cache", ".ruff_cache", "node_modules", ".venv", "venv"}


@tool
def list_directory(path: str = ".", depth: int = 2) -> str:
    """List the contents of a directory as a tree.

    Args:
        path:  Directory to list. Defaults to current directory.
        depth: How many levels deep to recurse. Default 2, max 5.

    Returns:
        A tree-style listing of files and directories.
    """
    try:
        depth = min(max(depth, 1), 5)
        target = _resolve(path)
        if not target.exists():
            return f"Error: Path not found: {path}"
        if not target.is_dir():
            return f"Error: Not a directory: {path}"

        lines: list[str] = [f"Directory: {target}"]
        _tree(target, lines, prefix="", current_depth=0, max_depth=depth)
        return "\n".join(lines)
    except PermissionError:
        return f"Error: Permission denied listing {path}"
    except Exception as exc:
        return f"Error listing {path}: {exc}"


def _tree(directory: Path, lines: list[str], prefix: str, current_depth: int, max_depth: int) -> None:
    if current_depth >= max_depth:
        return
    try:
        entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        lines.append(f"{prefix}  [permission denied]")
        return

    entries = [e for e in entries if e.name not in _IGNORED]
    for i, entry in enumerate(entries):
        connector = "└── " if i == len(entries) - 1 else "├── "
        if entry.is_dir():
            lines.append(f"{prefix}{connector}{entry.name}/")
            extension = "    " if i == len(entries) - 1 else "│   "
            _tree(entry, lines, prefix + extension, current_depth + 1, max_depth)
        else:
            size = entry.stat().st_size
            size_str = f"{size:,} B" if size < 1024 else f"{size / 1024:.1f} KB"
            lines.append(f"{prefix}{connector}{entry.name}  ({size_str})")


@tool
def search_files(
    pattern: str,
    path: str = ".",
    include: Optional[str] = None,
    case_sensitive: bool = False,
    max_results: int = 50,
) -> str:
    """Search for a regex pattern across files in a directory.

    Args:
        pattern:        Regex pattern to search for.
        path:           Root directory to search from. Default current directory.
        include:        Glob pattern to filter files, e.g. "*.py" or "**/*.ts".
        case_sensitive: Whether the search is case-sensitive. Default False.
        max_results:    Maximum number of matching lines to return. Default 50.

    Returns:
        Matching lines with file path and line number, or an error message.
    """
    try:
        root = _resolve(path)
        if not root.exists():
            return f"Error: Path not found: {path}"

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return f"Error: Invalid regex pattern: {e}"

        candidates = list(root.rglob(include)) if include else [p for p in root.rglob("*") if p.is_file()]
        results: list[str] = []
        searched = 0

        for file in sorted(candidates):
            if any(part in _IGNORED for part in file.parts):
                continue
            if not file.is_file():
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except (PermissionError, OSError):
                continue

            searched += 1
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    rel = file.relative_to(root)
                    results.append(f"{rel}:{lineno}: {line.rstrip()}")
                    if len(results) >= max_results:
                        break
            if len(results) >= max_results:
                break

        if not results:
            return f"No matches found for '{pattern}' in {searched} file(s)."
        return f"Found {len(results)} match(es) across {searched} file(s):\n" + "\n".join(results)
    except Exception as exc:
        return f"Error searching files: {exc}"
