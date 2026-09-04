"""Persistent memory system for hash-cli.

Stores facts, preferences, and learned rules in ~/.hash-cli/memory.json.
Memory is automatically injected into the system prompt each session.

The agent can update memory by calling update_memory() — exposed as a tool.
Memory is also auto-extracted at the end of each turn from the conversation.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

MEMORY_DIR  = Path.home() / ".hash-cli"
MEMORY_FILE = MEMORY_DIR / "memory.json"

_DEFAULT: dict[str, Any] = {
    "preferences":  [],   # e.g. "prefers snake_case", "uses pytest"
    "rules":        [],   # e.g. "always add type hints", "never use var"
    "facts":        [],   # e.g. "project uses FastAPI", "Python 3.11"
    "corrections":  [],   # things the user explicitly corrected
    "updated_at":   None,
}


def _load() -> dict[str, Any]:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _DEFAULT.copy()


def _save(data: dict[str, Any]) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now().isoformat()
    MEMORY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_memory() -> dict[str, Any]:
    return _load()


# ---------------------------------------------------------------------------
# Memory tool — the agent calls this directly
# ---------------------------------------------------------------------------

@tool
def update_memory(
    category: str,
    item: str,
    action: str = "add",
) -> str:
    """Store or remove a piece of information in long-term memory.

    Call this when the user expresses a preference, corrects you, states a
    project fact, or sets a rule — so you can apply it in future sessions.

    Args:
        category: One of "preferences", "rules", "facts", "corrections".
        item:     The string to remember or forget.
        action:   "add" to store it, "remove" to delete it.

    Returns:
        Confirmation message.

    Examples:
        update_memory("rules", "Always add type hints to Python functions", "add")
        update_memory("preferences", "User prefers pytest over unittest", "add")
        update_memory("facts", "Project uses FastAPI + PostgreSQL", "add")
        update_memory("corrections", "Never use print() for logging; use loguru", "add")
    """
    valid_categories = {"preferences", "rules", "facts", "corrections"}
    if category not in valid_categories:
        return f"Error: category must be one of {valid_categories}"

    data = _load()
    lst: list = data.setdefault(category, [])

    if action == "add":
        if item not in lst:
            lst.append(item)
            _save(data)
            return f"✓ Remembered ({category}): {item}"
        return f"Already knew: {item}"

    elif action == "remove":
        if item in lst:
            lst.remove(item)
            _save(data)
            return f"✓ Forgot ({category}): {item}"
        return f"Not found in {category}: {item}"

    return f"Error: action must be 'add' or 'remove'"


# ---------------------------------------------------------------------------
# Memory prompt injection
# ---------------------------------------------------------------------------

def build_memory_block() -> str:
    """Return a formatted memory block to inject into the system prompt."""
    data = _load()

    sections: list[str] = []

    if data.get("rules"):
        sections.append("### Rules (always follow these)\n" +
                         "\n".join(f"- {r}" for r in data["rules"]))

    if data.get("preferences"):
        sections.append("### User preferences\n" +
                         "\n".join(f"- {p}" for p in data["preferences"]))

    if data.get("facts"):
        sections.append("### Project / environment facts\n" +
                         "\n".join(f"- {f}" for f in data["facts"]))

    if data.get("corrections"):
        sections.append("### Past corrections (do not repeat these mistakes)\n" +
                         "\n".join(f"- {c}" for c in data["corrections"]))

    if not sections:
        return ""

    ts = data.get("updated_at", "")
    header = f"## Memory (learned from previous sessions — last updated: {ts})"
    return header + "\n\n" + "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Auto-extract learnable facts from a conversation turn
# ---------------------------------------------------------------------------

# Phrases that signal a rule or preference the agent should remember
_RULE_TRIGGERS = re.compile(
    r'\b(always|never|don\'t|do not|please|from now on|remember|make sure|'
    r'stop|prefer|i want|i need|i like|i hate|use|avoid)\b',
    re.IGNORECASE,
)


def maybe_extract_memory(user_text: str, agent_reply: str) -> list[tuple[str, str]]:
    """Heuristically detect learnable facts in the conversation.

    Returns list of (category, item) tuples the agent SHOULD store.
    These are soft suggestions — the agent decides via update_memory tool.
    """
    suggestions: list[tuple[str, str]] = []
    text = user_text.strip()

    # Short direct instructions → likely a rule
    if len(text) < 120 and _RULE_TRIGGERS.search(text):
        # Avoid trivial conversational phrases
        noise = {"ok", "thanks", "thank you", "sure", "yes", "no",
                 "good", "great", "done", "got it"}
        if text.lower().strip(".!?,") not in noise:
            suggestions.append(("rules", text))

    return suggestions


# ---------------------------------------------------------------------------
# CLI helpers for /memory command
# ---------------------------------------------------------------------------

def format_memory_for_display() -> str:
    """Return memory contents as a formatted string for the /memory command."""
    data = _load()
    lines = []

    for cat in ("rules", "preferences", "facts", "corrections"):
        items = data.get(cat, [])
        if items:
            lines.append(f"\n  {cat.upper()}")
            for item in items:
                lines.append(f"    • {item}")

    if not lines:
        return "  No memory stored yet."

    ts = data.get("updated_at", "never")
    return f"  Last updated: {ts}\n" + "\n".join(lines)


def clear_memory_category(category: str) -> str:
    data = _load()
    if category == "all":
        for cat in ("rules", "preferences", "facts", "corrections"):
            data[cat] = []
        _save(data)
        return "✓ All memory cleared."
    if category in data:
        data[category] = []
        _save(data)
        return f"✓ Cleared {category}."
    return f"Unknown category: {category}"
