# hash-cli

A local-first agentic AI assistant for the terminal, powered by **Ollama** and **LangGraph**.

It reads files, writes code, edits existing files surgically, runs shell commands, searches your codebase, browses the web, and generates documents (Excel, PDF, YAML, CSV) — all driven by a local LLM, with no API keys and no data leaving your machine.

```
  ██╗  ██╗ █████╗ ███████╗██╗  ██╗
  ██║  ██║██╔══██╗██╔════╝██║  ██║
  ███████║███████║███████╗███████║
  ██╔══██║██╔══██║╚════██║██╔══██║
  ██║  ██║██║  ██║███████║██║  ██║
  ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
```

---

## Requirements

| Requirement | Version |
|---|---|
| Python | ≥ 3.11 |
| [Ollama](https://ollama.com) | latest |
| A tool-capable model | see below |

hash-cli requires a model that supports **tool / function calling**. Good options:

| Model | Pull command | Notes |
|---|---|---|
| `qwen2.5-coder:7b` | `ollama pull qwen2.5-coder:7b` | Default. Best for coding tasks. |
| `llama3.1:8b` | `ollama pull llama3.1:8b` | General purpose, solid tool use. |
| `mistral-nemo` | `ollama pull mistral-nemo` | Fast, good tool use. |
| `deepseek-coder-v2` | `ollama pull deepseek-coder-v2` | Excellent for large codebases. |

---

## Installation

### Option A — Install from package (recommended)

**macOS** (Intel + Apple Silicon):
```bash
sudo installer -pkg hash-cli-0.1.0-macos-universal.pkg -target /
```
Then open a new terminal and type `hash-cli`.

**Windows** (x86_64):
```
Double-click hash-cli-0.1.0-windows-x86_64-setup.exe
```
Or silent install:
```powershell
.\hash-cli-0.1.0-windows-x86_64-setup.exe /S
```
Then open a new CMD or PowerShell and type `hash-cli`.

### Option B — Install from source (Python)

```bash
# Clone / navigate to the project
cd hash-cli

# Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate       # macOS / Linux
# .venv\Scripts\activate        # Windows

# Install
pip install -e .
```

The `hash-cli` command is now available in your shell.

### 1. Start Ollama (if not already running)

```bash
ollama serve
```

> hash-cli detects whether Ollama is running at startup. If it isn't, it will
> automatically open a second terminal window and start `ollama serve` for you.

### 2. Pull a model

```bash
ollama pull qwen2.5-coder:7b
```

---

## Usage

### Interactive session

```bash
hash-cli
```

Starts a REPL in your current directory. Type your task in plain English and press Enter.

### One-shot prompt

```bash
hash-cli "add type hints to all functions in src/utils.py"
```

Runs the agent once, prints the result, and exits.

### Options

```
hash-cli [OPTIONS] [PROMPT]

Options:
  -m, --model TEXT        Ollama model to use  [default: qwen2.5-coder:7b]
  --base-url TEXT         Ollama server URL    [default: http://localhost:11434]
  -C, --cwd PATH          Working directory for the session
  --stream / --no-stream  Stream responses token-by-token  [default: stream]
  -q, --quiet             Suppress the welcome banner
  --help                  Show this message and exit
```

### Examples

```bash
# Use a different model
hash-cli --model llama3.1:8b

# Start in a specific project directory
hash-cli --cwd ~/projects/my-app

# One-shot: create a new file
hash-cli "create a FastAPI hello-world app in main.py"

# One-shot: run tests and fix failures
hash-cli "run pytest and fix any failing tests"

# Disable streaming (useful for piping output)
hash-cli --no-stream "explain what this project does"

# Research a third-party tool
hash-cli "search the Celonis docs and explain how PQL FILTER works"
```

---

## Slash commands

Type these inside the interactive session:

| Command | Description |
|---|---|
| `/help` | Show command reference |
| `/quit` or `exit` | End the session |
| `/clear` | Clear the screen |
| `/history` | Show how many turns are in the current session |
| `/model <name>` | Switch to a different Ollama model mid-session |
| `/cwd <path>` | Change the working directory mid-session |
| `/tools` | List all available tools |

---

## Tools

The agent has access to twelve tools:

| Tool | Description |
|---|---|
| `read_file` | Read a file with optional line offset and limit |
| `write_file` | Create or overwrite a file (auto-creates parent dirs) |
| `edit_file` | Targeted string replacement inside an existing file |
| `list_directory` | Tree-view of a directory up to N levels deep |
| `search_files` | Regex search across files with optional glob filter |
| `run_command` | Execute shell commands (git, npm, pip, tests, …) |
| `web_search` | DuckDuckGo search for docs, error messages, packages |
| `web_fetch` | Fetch and extract full text from any URL |
| `create_excel` | Create an Excel (.xlsx) workbook with sheets and data |
| `create_pdf` | Create a PDF document with headings, paragraphs, tables |
| `create_yaml` | Write structured data to a YAML file |
| `create_csv` | Write tabular data to a CSV file |

---

## Architecture

```
hash-cli/
├── hash_cli/
│   ├── cli.py              # Typer entry point, REPL loop, session management
│   ├── ollama_launcher.py  # Auto-detects and launches Ollama if not running
│   ├── agent/
│   │   ├── graph.py        # LangGraph ReAct loop (agent ↔ tools cycle)
│   │   ├── state.py        # AgentState (typed message history)
│   │   └── prompts.py      # System prompt (injected with env context)
│   ├── tools/
│   │   ├── file_tools.py   # read_file, write_file, edit_file, list_directory, search_files
│   │   ├── shell_tools.py  # run_command
│   │   ├── web_tools.py    # web_search, web_fetch
│   │   └── doc_tools.py    # create_excel, create_pdf, create_yaml, create_csv
│   └── ui/
│       ├── console.py      # HashConsole (Rich rendering, streaming, panels)
│       └── theme.py        # Colour palette and spinner config
├── packaging/
│   ├── macos/              # build_mac.sh → universal .pkg
│   └── windows/            # build_windows.ps1 → NSIS .exe installer
└── pyproject.toml
```

The agent loop is a two-node LangGraph state machine:

```
         ┌─────────┐
 input → │  agent  │ ──── has tool calls? ──── yes ──→ ┌───────┐
         └─────────┘                                     │ tools │
              ↑                                          └───────┘
              └──────────────────────────────────────────────┘
                           (loop until no tool calls)
```

When the agent produces a plain-text reply with no tool calls, the graph exits and the response is rendered to the terminal.

---

## Building the installers

### macOS `.pkg` (run on a Mac)

```bash
pip install pyinstaller
bash packaging/macos/build_mac.sh
# → dist/hash-cli-0.1.0-macos-universal.pkg
```

### Windows `.exe` (run on Windows)

```powershell
pip install pyinstaller
# Install NSIS from https://nsis.sourceforge.io
.\packaging\windows\build_windows.ps1
# → dist\hash-cli-0.1.0-windows-x86_64-setup.exe
```

---

## Configuration

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `HASH_DEBUG` | unset | Set to any value to print full tracebacks on errors |

### Changing the default model

Pass `--model` on every invocation, or open `hash_cli/cli.py` and change the option default:

```python
model: str = typer.Option(
    "llama3.1:8b",   # ← change this
    ...
)
```

---

## Development

```bash
# Install dev dependencies (optional linting/formatting)
pip install ruff mypy

# Lint
ruff check hash_cli/

# Type-check
mypy hash_cli/ --ignore-missing-imports

# Run directly without installing
python -m hash_cli.cli
```

---

## Troubleshooting

**`Cannot reach Ollama at http://localhost:11434`**
Ollama isn't running. hash-cli will try to start it automatically, or you can run `ollama serve` manually.

**`Model 'xyz' not found locally`**
Pull the model first: `ollama pull xyz`

**Agent calls tools but produces no text reply**
Some smaller models don't follow tool-use conventions reliably. Switch to a larger or coding-specific model like `qwen2.5-coder:7b` or `deepseek-coder-v2`.

**Commands time out**
The default shell timeout is 60 seconds. For long-running builds you can increase `_TIMEOUT` in `hash_cli/tools/shell_tools.py`.

---

## License

MIT
