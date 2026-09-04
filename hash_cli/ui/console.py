"""HashConsole — the primary Rich-based UI layer for hash-cli."""

from __future__ import annotations

import re
from typing import Iterator

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from hash_cli.agent.graph import StreamEvent
from hash_cli.ui.theme import BORDER_STYLE, HASH_THEME

_RAW_TOOL_RE = re.compile(
    r'\{[^{}]*"(?:name|tool)"\s*:\s*"[^"]+"\s*,.*?\}',
    re.DOTALL,
)

# Also strip <tool_response>...</tool_response> blocks some models emit
_TOOL_RESP_RE = re.compile(
    r'<tool_response>.*?</tool_response>',
    re.DOTALL,
)


def _strip_raw_tool_json(text: str) -> str:
    cleaned = _RAW_TOOL_RE.sub("", text)
    cleaned = _TOOL_RESP_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


class HashConsole:
    """Handles all terminal rendering for hash-cli."""

    def __init__(self, *, quiet: bool = False) -> None:
        self._console = Console(theme=HASH_THEME, highlight=False)
        self._quiet = quiet

    # ------------------------------------------------------------------
    # Welcome banner
    # ------------------------------------------------------------------

    def print_welcome(
        self,
        model: str,
        cwd: str,
        provider: str = "ollama",
        ollama_status: str = "",
    ) -> None:
        logo = Text()
        logo.append("  ██╗  ██╗ █████╗ ███████╗██╗  ██╗\n", style="hash.brand")
        logo.append("  ██║  ██║██╔══██╗██╔════╝██║  ██║\n", style="hash.brand")
        logo.append("  ███████║███████║███████╗███████║\n",  style="hash.brand")
        logo.append("  ██╔══██║██╔══██║╚════██║██╔══██║\n", style="hash.accent")
        logo.append("  ██║  ██║██║  ██║███████║██║  ██║\n", style="hash.accent")
        logo.append("  ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝", style="hash.accent")

        info = Table.grid(padding=(0, 1))
        info.add_column(style="hash.dim")
        info.add_column()
        info.add_row("model",    f"[hash.info]{model}[/hash.info]")
        info.add_row("provider", f"[hash.accent]{provider}[/hash.accent]")
        info.add_row("cwd",      f"[hash.dim]{cwd}[/hash.dim]")
        if ollama_status:
            style = "hash.success" if "✓" in ollama_status else "hash.warning"
            info.add_row("ollama", f"[{style}]{ollama_status}[/{style}]")
        info.add_row("tools",  "[hash.dim]read · write · edit · delete · shell · packages · excel · pdf · yaml · csv · search · web · memory[/hash.dim]")
        info.add_row("model",  "[hash.dim]/model  to switch model[/hash.dim]")
        info.add_row("quit",   "[hash.dim]exit  or  Ctrl+D[/hash.dim]")
        info.add_row("input",  "[hash.dim]Option+Enter for newline · Up/Down for history[/hash.dim]")

        self._console.print()
        self._console.print(
            Panel(
                Columns([logo, info], equal=False, expand=False),
                border_style="hash.brand",
                box=box.ROUNDED,
                padding=(1, 2),
            )
        )
        self._console.print()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def setup_input(self) -> None:
        from hash_cli.ui.input import create_session
        self._pt_session = create_session()

    def prompt(self) -> str:
        from hash_cli.ui.input import create_session, read_input
        if not hasattr(self, "_pt_session"):
            self._pt_session = create_session()
        try:
            return read_input(self._pt_session)
        except EOFError:
            return "/quit"

    def prompt_raw(self, label: str = "  › ") -> str:
        """Simple raw input for menus/API key entry (no prompt_toolkit)."""
        try:
            return input(label)
        except (EOFError, KeyboardInterrupt):
            return ""

    # ------------------------------------------------------------------
    # Real-time streaming response (spinner actually animates)
    # ------------------------------------------------------------------

    def stream_response_rt(self, events) -> tuple:
        """Consume a real-time event iterator from stream_agent_realtime.

        Returns (updated_history, usage_stats_or_None).
        """
        from hash_cli.agent.graph import UsageStats
        full_text = ""
        tool_log: list = []
        captured_usage: UsageStats | None = None

        with Live(
            Spinner("dots", text="  Thinking…", style="hash.accent"),
            console=self._console,
            refresh_per_second=20,
            transient=True,
        ) as live:
            for event in events:
                if event.kind == "tool_start":
                    live.update(Spinner(
                        "dots2",
                        text=f"  {event.tool_name}…",
                        style="hash.tool_name",
                    ))
                    tool_log.append(("start", event.tool_name, event.tool_input))

                elif event.kind == "tool_end":
                    tool_log.append(("end", event.tool_name, event.tool_output))
                    live.update(Spinner("dots", text="  Thinking…", style="hash.accent"))

                elif event.kind == "token":
                    full_text += event.content

                elif event.kind == "usage":
                    captured_usage = event.usage

                elif event.kind == "error":
                    live.stop()
                    err = event.content
                    # Detect out-of-memory / model-crash errors and give guidance
                    if any(sig in err.lower() for sig in
                           ("killed", "status code: 500", "out of memory", "oom", "terminated")):
                        self._console.print(
                            "[hash.error]✗  The model crashed — likely out of memory.[/hash.error]\n"
                            "[hash.dim]   This model is too large for your machine's RAM.\n"
                            "   Switch to a smaller model:  /model  → pick Llama 3.1 8B or Qwen 2.5 Coder\n"
                            "   Or use a cloud model (DeepSeek V4 Flash) which doesn't use local RAM.[/hash.dim]"
                        )
                    else:
                        self._console.print(
                            f"[hash.error]✗  {escape(err)}[/hash.error]"
                        )
                    getter = getattr(events, "get_history", None)
                    history = getter() if callable(getter) else []
                    return history, None

        if tool_log:
            self._print_tool_summary(tool_log)

        clean = _strip_raw_tool_json(full_text)
        if clean:
            self._print_assistant(clean)

        getter = getattr(events, "get_history", None)
        history = getter() if callable(getter) else []
        return history, captured_usage

    def print_turn_stats(
        self,
        elapsed: float,
        usage: "UsageStats | None" = None,
        session_total: int = 0,
    ) -> None:
        """Print a compact one-line stats bar after each turn.

        Shows response time always, token usage only for premium models.
        """
        from hash_cli.agent.graph import UsageStats
        from rich.text import Text

        # Format elapsed time
        if elapsed < 1:
            time_str = f"{elapsed * 1000:.0f}ms"
        elif elapsed < 60:
            time_str = f"{elapsed:.1f}s"
        else:
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            time_str = f"{mins}m {secs}s"

        line = Text("  ")
        line.append("⏱ ", style="hash.dim")
        line.append(time_str, style="hash.accent")

        if usage and not usage.is_empty():
            total = usage.input_tokens + usage.output_tokens
            if total > 0:
                in_pct  = usage.input_tokens  / total
                out_pct = usage.output_tokens / total
            else:
                in_pct = out_pct = 0.5

            BAR = 12
            in_fill  = max(1, round(in_pct  * BAR))
            out_fill = max(1, BAR - in_fill)

            line.append("   ", style="hash.dim")
            line.append("▏", style="hash.dim")
            line.append("▓" * in_fill,  style="hash.info")
            line.append("▓" * out_fill, style="hash.accent")
            line.append("▏", style="hash.dim")
            line.append(
                f"  {usage.input_tokens:,} in  {usage.output_tokens:,} out"
                f"  = {usage.total_tokens:,} tokens",
                style="hash.dim",
            )
            if session_total:
                line.append(f"  │  session: {session_total:,}", style="hash.dim")

        self._console.print(line)
    # ------------------------------------------------------------------
    # Tool summary + assistant panel
    # ------------------------------------------------------------------

    def _print_tool_summary(self, tool_log: list) -> None:
        lines = Text()
        for entry in tool_log:
            kind, name, payload = entry
            if kind == "start":
                desc = _tool_human_desc(name, payload)
                lines.append(f"  ⚙  {name}", style="hash.tool_name")
                lines.append(f"  {desc}\n", style="hash.tool_io")
            elif kind == "end":
                preview = str(payload).strip().splitlines()[0][:120]
                lines.append(f"     ↳ {escape(preview)}\n", style="hash.tool_ok")

        self._console.print(
            Panel(
                lines,
                title="[hash.dim]tool activity[/hash.dim]",
                title_align="left",
                border_style=BORDER_STYLE,
                box=box.SIMPLE_HEAD,
                padding=(0, 1),
            )
        )

    def _print_assistant(self, text: str) -> None:
        try:
            body = Markdown(text, code_theme="one-dark")
        except Exception:
            body = Text(text)
        self._console.print(
            Panel(
                body,
                title="[hash.assistant]hash[/hash.assistant]",
                border_style="hash.brand",
                title_align="left",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def print_info(self, message: str) -> None:
        self._console.print(f"[hash.info]ℹ  {escape(message)}[/hash.info]")

    def print_success(self, message: str) -> None:
        self._console.print(f"[hash.success]✓  {escape(message)}[/hash.success]")

    def print_warning(self, message: str) -> None:
        self._console.print(f"[hash.warning]⚠  {escape(message)}[/hash.warning]")

    def print_error(self, message: str) -> None:
        self._console.print(f"[hash.error]✗  {escape(message)}[/hash.error]")

    def print_rule(self, title: str = "") -> None:
        self._console.print(Rule(title, style=BORDER_STYLE))

    # ------------------------------------------------------------------
    # /help and /tools
    # ------------------------------------------------------------------

    def print_help(self) -> None:
        table = Table(
            show_header=True, header_style="hash.tool_name",
            box=box.SIMPLE_HEAD, border_style=BORDER_STYLE, padding=(0, 1),
        )
        table.add_column("Command",     style="hash.accent", no_wrap=True)
        table.add_column("Description", style="hash.dim")
        rows = [
            ("/help",               "Show this help message"),
            ("/quit  or  exit",     "End the session"),
            ("/clear",              "Clear the screen"),
            ("/model",              "Switch AI model (Ollama free / OpenAI / Anthropic / Google)"),
            ("/setup",              "Re-run first-time setup (install Ollama, pull a model)"),
            ("/pull <model>",       "Download an Ollama model (e.g. /pull llama3.2:3b)"),
            ("/key list",           "Show stored API keys and their status"),
            ("/key set <ENV>",      "Add or reset an API key (e.g. /key set OPENAI_API_KEY)"),
            ("/key remove <ENV>",   "Delete a stored API key"),
            ("/mcp list",           "List configured MCP servers"),
            ("/mcp new <name>",     "Add a new MCP server (guided prompt)"),
            ("/mcp test <name>",    "Test an MCP server connection"),
            ("/mcp json",           "Show raw mcp.json content"),
            ("/mcp remove <name>",  "Remove an MCP server"),
            ("/memory",             "Show learned memory"),
            ("/memory clear all",   "Clear all memory"),
            ("/history",            "Show conversation turn count"),
            ("/tools",              "List available tools"),
            ("/cwd <path>",         "Change working directory"),
            ("Option+Enter",        "Insert newline in your message"),
            ("Up / Down",           "Navigate message history"),
            ("Ctrl+D",              "Exit hash-cli"),
        ]
        for cmd, desc in rows:
            table.add_row(cmd, desc)
        self._console.print(
            Panel(table, title="[hash.brand]hash-cli — Commands[/hash.brand]",
                  border_style="hash.brand", box=box.ROUNDED, padding=(0, 1))
        )

    def print_tools(self, tools: list) -> None:
        table = Table(
            show_header=True, header_style="hash.tool_name",
            box=box.SIMPLE_HEAD, border_style=BORDER_STYLE, padding=(0, 1),
        )
        table.add_column("Tool",        style="hash.accent", no_wrap=True)
        table.add_column("Description", style="hash.dim")
        for t in tools:
            name = getattr(t, "name", str(t))
            desc = (getattr(t, "description", "") or "").splitlines()[0][:80]
            table.add_row(name, desc)
        self._console.print(
            Panel(table, title="[hash.brand]Available Tools[/hash.brand]",
                  border_style="hash.brand", box=box.ROUNDED, padding=(0, 1))
        )

    def clear(self) -> None:
        self._console.clear()

    def print(self, *args, **kwargs) -> None:
        self._console.print(*args, **kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_human_desc(tool_name: str, args: dict) -> str:
    if not args:
        return ""
    if tool_name == "write_file":
        return f"→ {args.get('path', '')}  ({len((args.get('content','')).splitlines())} lines)"
    if tool_name in ("read_file", "edit_file", "delete_file"):
        return f"→ {args.get('path', '')}"
    if tool_name == "run_command":
        return f"$ {escape(args.get('command', '')[:80])}"
    if tool_name == "manage_packages":
        return f"{args.get('action','')} {', '.join(args.get('packages',[]))}"
    if tool_name == "list_directory":
        return f"→ {args.get('path', '.')}"
    if tool_name == "search_files":
        return f"pattern={escape(str(args.get('pattern',''))[:40])}"
    if tool_name == "web_search":
        return f'"{escape(str(args.get("query",""))[:60])}"'
    if tool_name == "web_fetch":
        return f"→ {args.get('url','')[:70]}"
    if tool_name in ("create_excel","create_pdf","create_yaml","create_csv"):
        return f"→ {args.get('path','')}"
    if tool_name == "update_memory":
        return f"{args.get('action','add')} [{args.get('category','')}] {str(args.get('item',''))[:50]}"
    parts = [f"{k}={escape(str(v)[:40])}" for k, v in list(args.items())[:3]]
    return "  ".join(parts)

    """Handles all terminal rendering for hash-cli."""

    def __init__(self, *, quiet: bool = False) -> None:
        self._console = Console(theme=HASH_THEME, highlight=False)
        self._quiet = quiet

    # ------------------------------------------------------------------
    # Welcome banner
    # ------------------------------------------------------------------

    def print_welcome(self, model: str, cwd: str, ollama_status: str = "running") -> None:
        logo = Text()
        logo.append("  ██╗  ██╗ █████╗ ███████╗██╗  ██╗\n", style="hash.brand")
        logo.append("  ██║  ██║██╔══██╗██╔════╝██║  ██║\n", style="hash.brand")
        logo.append("  ███████║███████║███████╗███████║\n",  style="hash.brand")
        logo.append("  ██╔══██║██╔══██║╚════██║██╔══██║\n", style="hash.accent")
        logo.append("  ██║  ██║██║  ██║███████║██║  ██║\n", style="hash.accent")
        logo.append("  ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝", style="hash.accent")

        ollama_style = "hash.success" if "running" in ollama_status else "hash.warning"

        info = Table.grid(padding=(0, 1))
        info.add_column(style="hash.dim")
        info.add_column()
        info.add_row("model",  f"[hash.info]{model}[/hash.info]")
        info.add_row("cwd",    f"[hash.dim]{cwd}[/hash.dim]")
        info.add_row("ollama", f"[{ollama_style}]{ollama_status}[/{ollama_style}]")
        info.add_row("tools",  "[hash.dim]read · write · edit · shell · excel · pdf · yaml · csv · search · web[/hash.dim]")
        info.add_row("quit",   "[hash.dim]type  exit  or  /quit  or  Ctrl+C[/hash.dim]")

        self._console.print()
        self._console.print(
            Panel(
                Columns([logo, info], equal=False, expand=False),
                border_style="hash.brand",
                box=box.ROUNDED,
                padding=(1, 2),
            )
        )
        self._console.print()

    # ------------------------------------------------------------------
    # Input prompt — powered by prompt_toolkit
    # ------------------------------------------------------------------

    def setup_input(self) -> None:
        """Call once at session start to initialise the prompt_toolkit session."""
        from hash_cli.ui.input import create_session
        self._pt_session = create_session()

    def prompt(self) -> str:
        """Read user input using prompt_toolkit.

        Features:
          - Unlimited paste with no line limit
          - Text selection and delete (mouse + keyboard)
          - Shift+Enter to insert a newline, Enter to submit
          - Up/Down arrows for history
          - Ctrl+A select all, Backspace/Delete on selection
        """
        from hash_cli.ui.input import create_session, read_input

        # Lazy init in case setup_input wasn't called
        if not hasattr(self, "_pt_session"):
            self._pt_session = create_session()

        try:
            return read_input(self._pt_session)
        except EOFError:
            return "/quit"

    # ------------------------------------------------------------------
    # Message rendering
    # ------------------------------------------------------------------

    def stream_response(self, events: Iterator[StreamEvent]) -> str:
        """Consume StreamEvents, render a persistent animated spinner,
        then print the final assistant reply. Returns the final text."""

        full_text = ""
        tool_log: list = []

        # ── Phase 1: animated spinner while events are consumed ───────
        with Live(
            Spinner("dots", text="  Thinking…", style="hash.accent"),
            console=self._console,
            refresh_per_second=20,
            transient=True,
        ) as live:
            for event in events:
                if event.kind == "tool_start":
                    label = f"  Running {event.tool_name}…"
                    live.update(Spinner("dots2", text=label, style="hash.tool_name"))
                    tool_log.append(("start", event.tool_name, event.tool_input))

                elif event.kind == "tool_end":
                    tool_log.append(("end", event.tool_name, event.tool_output))
                    live.update(Spinner("dots", text="  Thinking…", style="hash.accent"))

                elif event.kind == "token":
                    full_text += event.content

                elif event.kind == "error":
                    live.stop()
                    self._console.print(
                        f"[hash.error]✗  Agent error:[/hash.error] {escape(event.content)}"
                    )
                    return ""

        # ── Phase 2: tool activity summary ───────────────────────────
        if tool_log:
            self._print_tool_summary(tool_log)

        # ── Phase 3: clean and render the final reply ─────────────────
        clean = _strip_raw_tool_json(full_text)
        if clean:
            self._print_assistant(clean)

        return clean

    def _print_tool_summary(self, tool_log: list) -> None:
        lines = Text()
        for entry in tool_log:
            kind, name, payload = entry
            if kind == "start":
                # Show a human-readable description instead of raw args
                desc = _tool_human_desc(name, payload)
                lines.append(f"  ⚙  {name}", style="hash.tool_name")
                lines.append(f"  {desc}\n", style="hash.tool_io")
            elif kind == "end":
                preview = str(payload).strip().splitlines()[0][:120]
                lines.append(f"     ↳ {escape(preview)}\n", style="hash.tool_ok")

        self._console.print(
            Panel(
                lines,
                title="[hash.dim]tool activity[/hash.dim]",
                title_align="left",
                border_style=BORDER_STYLE,
                box=box.SIMPLE_HEAD,
                padding=(0, 1),
            )
        )

    def _print_assistant(self, text: str) -> None:
        try:
            body = Markdown(text, code_theme="one-dark")
        except Exception:
            body = Text(text)

        self._console.print(
            Panel(
                body,
                title="[hash.assistant]hash[/hash.assistant]",
                border_style="hash.brand",
                title_align="left",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def print_info(self, message: str) -> None:
        self._console.print(f"[hash.info]ℹ  {escape(message)}[/hash.info]")

    def print_success(self, message: str) -> None:
        self._console.print(f"[hash.success]✓  {escape(message)}[/hash.success]")

    def print_warning(self, message: str) -> None:
        self._console.print(f"[hash.warning]⚠  {escape(message)}[/hash.warning]")

    def print_error(self, message: str) -> None:
        self._console.print(f"[hash.error]✗  {escape(message)}[/hash.error]")

    def print_rule(self, title: str = "") -> None:
        self._console.print(Rule(title, style=BORDER_STYLE))

    # ------------------------------------------------------------------
    # /tools
    # ------------------------------------------------------------------

    def print_tools(self, tools: list) -> None:
        table = Table(
            show_header=True,
            header_style="hash.tool_name",
            box=box.SIMPLE_HEAD,
            border_style=BORDER_STYLE,
            padding=(0, 1),
        )
        table.add_column("Tool",        style="hash.accent", no_wrap=True)
        table.add_column("Description", style="hash.dim")

        for t in tools:
            name = getattr(t, "name", str(t))
            desc = (getattr(t, "description", "") or "").splitlines()[0][:80]
            table.add_row(name, desc)

        self._console.print(
            Panel(
                table,
                title="[hash.brand]Available Tools[/hash.brand]",
                border_style="hash.brand",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def clear(self) -> None:
        self._console.clear()

    def print(self, *args, **kwargs) -> None:
        self._console.print(*args, **kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_human_desc(tool_name: str, args: dict) -> str:
    """Return a human-readable one-liner for a tool call instead of raw args."""
    if not args:
        return ""

    # Per-tool pretty descriptions
    if tool_name == "write_file":
        path = args.get("path", "")
        lines = len((args.get("content", "")).splitlines())
        return f"→ {path}  ({lines} lines)"

    if tool_name == "read_file":
        path = args.get("path", "")
        offset = args.get("offset", 0)
        return f"→ {path}" + (f"  from line {offset}" if offset else "")

    if tool_name == "edit_file":
        path = args.get("path", "")
        return f"→ {path}"

    if tool_name == "run_command":
        cmd = args.get("command", "")[:80]
        return f"$ {escape(cmd)}"

    if tool_name == "list_directory":
        return f"→ {args.get('path', '.')}"

    if tool_name == "search_files":
        return f"pattern={escape(str(args.get('pattern', ''))[:40])}"

    if tool_name == "web_search":
        return f'"{escape(str(args.get("query", ""))[:60])}"'

    if tool_name == "web_fetch":
        return f"→ {args.get('url', '')[:70]}"

    if tool_name in ("create_excel", "create_pdf", "create_yaml", "create_csv"):
        return f"→ {args.get('path', '')}"

    # Fallback: key=value pairs, truncated
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 50:
            v_str = v_str[:47] + "…"
        parts.append(f"{k}={escape(v_str)}")
    return "  ".join(parts)

