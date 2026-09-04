"""Rich theme and colour palette for hash-cli."""

from rich.theme import Theme

HASH_THEME = Theme(
    {
        # Brand colours — electric cyan/teal for hash-cli
        "hash.brand":     "bold #00D9FF",       # bright cyan
        "hash.accent":    "#00B4CC",            # teal
        "hash.dim":       "dim #6B7280",        # muted grey

        # Message roles
        "hash.user":      "bold #60A5FA",       # blue
        "hash.assistant": "bold #00D9FF",       # cyan
        "hash.system":    "dim #9CA3AF",        # grey

        # Tool events
        "hash.tool_name": "bold #FBBF24",       # amber
        "hash.tool_ok":   "#34D399",            # green
        "hash.tool_err":  "bold #F87171",       # red
        "hash.tool_io":   "dim #D1D5DB",        # light grey

        # Status / misc
        "hash.success":   "bold #34D399",
        "hash.warning":   "bold #FBBF24",
        "hash.error":     "bold #F87171",
        "hash.info":      "#93C5FD",
        "hash.border":    "#374151",

        # Code / Markdown
        "markdown.code":  "#E5E7EB on #1F2937",
        "markdown.h1":    "bold #00D9FF",
        "markdown.h2":    "bold #00B4CC",
        "markdown.h3":    "bold #67E8F9",
    }
)

SPINNER = "dots"
BORDER_STYLE = "hash.border"
