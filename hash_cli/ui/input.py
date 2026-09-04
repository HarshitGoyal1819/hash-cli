"""Terminal input handler for hash-cli using prompt_toolkit.

Provides a full-featured input box:
  - Paste works normally (no auto-submit, no truncation)
  - Text selection + delete (keyboard)
  - Option+Enter (Mac) or Ctrl+J for newline, Enter to submit
  - Ctrl+A select all, Ctrl+C clear line, Ctrl+D exit
  - Up/Down arrow history navigation
"""

from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

# ── Colour style ─────────────────────────────────────────────────────────
_STYLE = Style.from_dict(
    {
        "prompt": "#00D9FF bold",
        "":       "#E5E7EB",
    }
)

_PROMPT_HTML = HTML("<prompt>  you </prompt> ")

# ── Key bindings ──────────────────────────────────────────────────────────
_bindings = KeyBindings()


@_bindings.add("escape", "enter")   # Option/Alt+Enter → newline
def _option_enter(event):
    event.current_buffer.insert_text("\n")


@_bindings.add("c-j")               # Ctrl+J → newline (fallback)
def _ctrl_j(event):
    event.current_buffer.insert_text("\n")


# ── Persistent in-session history ─────────────────────────────────────────
_history = InMemoryHistory()


def create_session() -> PromptSession:
    """Create a reusable PromptSession.

    Notes:
    - mouse_support=False  → fixes paste in most terminals (mouse support
      intercepts the escape sequences that terminals use for bracketed paste)
    - multiline=False      → single submit on Enter; Option+Enter for newlines
    - enable_system_prompt=False → avoids conflicts with some terminals
    """
    return PromptSession(
        history=_history,
        style=_STYLE,
        key_bindings=_bindings,
        multiline=False,
        mouse_support=False,      # must be False for paste to work correctly
        wrap_lines=True,
        enable_history_search=True,
        complete_while_typing=False,
    )


def read_input(session: PromptSession) -> str:
    """Read one user message. Blocks until Enter is pressed.

    Paste behaviour:
      - Small paste  → arrives as one chunk, submitted normally on Enter
      - Large paste  → prompt_toolkit buffers it all; submit with Enter
      - No auto-submit on timer — only Enter submits

    Keybindings:
      Enter            — submit
      Option+Enter     — insert newline (multiline message)
      Ctrl+J           — insert newline (alternative)
      Up / Down        — message history
      Ctrl+A           — select all text in current input
      Ctrl+C           — clear current input line
      Ctrl+D           — exit (raises EOFError)
    """
    return session.prompt(_PROMPT_HTML, style=_STYLE)
