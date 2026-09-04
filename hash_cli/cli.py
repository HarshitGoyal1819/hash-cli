"""hash-cli — entry point."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer
from langchain_core.messages import BaseMessage

from hash_cli.agent.graph import (
    AgentConfig, AgentState, StreamEvent, UsageStats,
    create_agent, run_agent, stream_agent_realtime,
)
from hash_cli.config import (
    MODELS, apply_api_keys_to_env, get_active_model_info,
    get_api_key, save_api_key, set_active_model,
    get_api_key_for_env, save_api_key_for_env,
    remove_api_key_for_env, list_stored_keys,
)
from hash_cli.ollama_launcher import ensure_ollama_running, is_ollama_running, stop_ollama
from hash_cli.tools import ALL_TOOLS
from hash_cli.ui import HashConsole

app = typer.Typer(
    name="hash-cli",
    help="hash-cli — a local-first agentic AI assistant.",
    add_completion=False,
    rich_markup_mode="rich",
    no_args_is_help=False,
    invoke_without_command=True,
)


# ---------------------------------------------------------------------------
# Model switcher UI
# ---------------------------------------------------------------------------

def _add_custom_model(console: "HashConsole", session: "Session") -> None:
    """Interactive wizard to add a custom premium model."""
    from hash_cli.config import add_custom_model, save_api_key_for_env, set_active_model

    console.print("\n[hash.brand]  ── Add a custom model ──[/hash.brand]\n")
    console.print("[hash.dim]  Add any model from a provider hash-cli supports.[/hash.dim]\n")

    # ── Provider ─────────────────────────────────────────────────────────
    console.print("  Provider:")
    console.print("  [hash.accent]1[/hash.accent]  OpenAI-compatible  (OpenAI, DeepSeek, Together, Groq, OpenRouter, local vLLM…)")
    console.print("  [hash.accent]2[/hash.accent]  Anthropic  (Claude)")
    console.print("  [hash.accent]3[/hash.accent]  Google  (Gemini)")
    p = console.prompt_raw("  provider [1/2/3] › ").strip()
    provider_map = {"1": "openai", "2": "anthropic", "3": "google"}
    provider = provider_map.get(p)
    if not provider:
        console.print_warning("Cancelled.")
        return

    # ── Model id ─────────────────────────────────────────────────────────
    console.print("\n[hash.dim]  Exact model name from the provider "
                  "(e.g. gpt-4o, claude-opus-4-8, deepseek-v4-pro, gemini-2.0-flash):[/hash.dim]")
    model_name = console.prompt_raw("  model › ").strip()
    if not model_name:
        console.print_warning("Cancelled — no model name.")
        return

    # ── Friendly label ───────────────────────────────────────────────────
    console.print("[hash.dim]  Display label (press Enter to use the model name):[/hash.dim]")
    label = console.prompt_raw("  label › ").strip() or model_name
    label = f"{label}  (premium, custom)"

    # ── Env var + optional base_url ──────────────────────────────────────
    default_env = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
    }[provider]

    base_url = ""
    if provider == "openai":
        console.print(
            f"\n[hash.dim]  API base URL — press Enter for OpenAI, or paste a custom one:\n"
            f"    DeepSeek:   https://api.deepseek.com/v1\n"
            f"    OpenRouter: https://openrouter.ai/api/v1\n"
            f"    Together:   https://api.together.xyz/v1\n"
            f"    Groq:       https://api.groq.com/openai/v1[/hash.dim]"
        )
        base_url = console.prompt_raw("  base_url › ").strip()
        # Custom endpoints usually want their own key env var
        if base_url:
            console.print(f"[hash.dim]  Env var name for this provider's key (Enter for {default_env}):[/hash.dim]")
            entered = console.prompt_raw("  env › ").strip()
            if entered:
                default_env = entered

    key_env = default_env
    model_id = f"custom/{provider}/{model_name}"

    entry = {
        "id":        model_id,
        "label":     label,
        "provider":  provider,
        "model":     model_name,
        "needs_key": True,
        "key_env":   key_env,
        "key_url":   "",
    }
    if base_url:
        entry["base_url"] = base_url

    ok, msg = add_custom_model(entry)
    if not ok:
        console.print_error(msg)
        return
    console.print_success(msg)

    # ── API key ──────────────────────────────────────────────────────────
    from hash_cli.config import get_api_key_for_env
    if not get_api_key_for_env(key_env):
        console.print(f"\n[hash.dim]  Paste the API key for {key_env} and press Enter:[/hash.dim]")
        key = console.prompt_raw("  key › ").strip()
        if key:
            save_api_key_for_env(key_env, key)
            console.print_success(f"Saved {key_env}.")

    # ── Offer to activate ────────────────────────────────────────────────
    console.print("\n[hash.dim]  Make this the active model now? (Y/n):[/hash.dim]")
    if console.prompt_raw("  › ").strip().lower() != "n":
        set_active_model(model_id)
        session.switch_model_from_config()
        if session.missing_key():
            console.print_warning("Model set, but the key seems missing/invalid.")
        else:
            console.print_success(f"{model_name} is now active.")
    console.print("")


def _read_secret(console: "HashConsole", label: str = "  › ") -> str:
    """Read a secret (API key) with masked input, reliably across terminals.

    Uses prompt_toolkit's is_password mode which works even when the terminal
    is managed by prompt_toolkit (unlike getpass, which can return empty).
    Falls back to plain visible input if prompt_toolkit fails.
    """
    try:
        from prompt_toolkit import prompt as pt_prompt
        return pt_prompt(label, is_password=True)
    except Exception:
        # Fallback: getpass, then plain input as last resort
        try:
            import getpass
            val = getpass.getpass(label)
            if val:
                return val
        except Exception:
            pass
        try:
            return input(label)
        except Exception:
            return ""


def _mcp_guided_setup(console: "HashConsole") -> None:
    """Interactive step-by-step wizard for adding a new MCP server."""
    import os
    from hash_cli.mcp_manager import add_server
    from hash_cli.mcp_client import test_connection

    console.print("\n[hash.brand]  ── Add MCP Server ──[/hash.brand]\n")

    # ── Step 1: Name ────────────────────────────────────────────────────
    console.print("[hash.dim]  Step 1/5 — Server name (e.g. celonis, stripe, github):[/hash.dim]")
    name = console.prompt_raw("  name › ").strip()
    if not name:
        console.print_warning("Cancelled.")
        return

    # ── Step 2: Transport type ───────────────────────────────────────────
    console.print("\n[hash.dim]  Step 2/5 — Server type:[/hash.dim]")
    console.print("  [hash.accent]1[/hash.accent]  HTTP / Remote URL  (Celonis, Stripe API, any web service)")
    console.print("  [hash.accent]2[/hash.accent]  stdio / Local process  (uvx, npx, python -m ...)")
    transport_choice = console.prompt_raw("  type [1/2] › ").strip()

    url = None
    command = None
    args_list = None
    env_dict = None
    transport = "auto"

    if transport_choice == "2":
        # stdio
        console.print("\n[hash.dim]  Command to run (e.g. uvx):[/hash.dim]")
        command = console.prompt_raw("  command › ").strip()
        if not command:
            console.print_warning("Cancelled.")
            return
        console.print("[hash.dim]  Arguments (space-separated, e.g. @stripe/agent-toolkit@latest):[/hash.dim]")
        args_raw = console.prompt_raw("  args › ").strip()
        args_list = args_raw.split() if args_raw else []
        console.print("[hash.dim]  Env vars (KEY=VALUE pairs, comma-separated, or leave blank):[/hash.dim]")
        env_raw = console.prompt_raw("  env › ").strip()
        env_dict = {}
        if env_raw:
            for pair in env_raw.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    env_dict[k.strip()] = v.strip()
    else:
        # HTTP
        console.print("\n[hash.dim]  Server URL:[/hash.dim]")
        url = console.prompt_raw("  url › ").strip()
        if not url:
            console.print_warning("Cancelled.")
            return
        console.print("[hash.dim]  Transport protocol:[/hash.dim]")
        console.print("  [hash.accent]1[/hash.accent]  auto  — try Streamable HTTP first, fall back to SSE (recommended)")
        console.print("  [hash.accent]2[/hash.accent]  sse   — legacy SSE (2024-11-05) — use for Celonis and older servers")
        console.print("  [hash.accent]3[/hash.accent]  streamable-http — modern (2025-03-26+)")
        tp = console.prompt_raw("  protocol [1/2/3] › ").strip()
        transport = {"2": "sse", "3": "streamable-http"}.get(tp, "auto")

    # ── Step 3: Auth type ────────────────────────────────────────────────
    console.print("\n[hash.dim]  Step 3/5 — Authentication:[/hash.dim]")
    console.print("  [hash.accent]1[/hash.accent]  none           — no auth")
    console.print("  [hash.accent]2[/hash.accent]  bearer         — Bearer token (most common for SaaS)")
    console.print("  [hash.accent]3[/hash.accent]  api_key        — Custom header API key")
    console.print("  [hash.accent]4[/hash.accent]  oauth2_token   — Pre-obtained OAuth2 access token")
    console.print("  [hash.accent]5[/hash.accent]  oauth2_client  — OAuth2 Client Credentials (machine-to-machine)")
    console.print("  [hash.accent]6[/hash.accent]  oauth2_pkce    — OAuth2 Authorization Code + PKCE (opens browser)")
    auth_choice = console.prompt_raw("  auth [1-6] › ").strip()

    auth_map = {
        "1": "none", "2": "bearer", "3": "api_key",
        "4": "oauth2_token", "5": "oauth2_client", "6": "oauth2_pkce"
    }
    auth_type = auth_map.get(auth_choice, "none")

    # Collect auth params
    token = refresh_tok = api_key = api_key_header = None
    tok_url = client_id = client_secret = scope = auth_url = None

    import getpass

    if auth_type == "bearer":
        console.print("[hash.dim]  Bearer token (masked):[/hash.dim]")
        token = _read_secret(console, "  token › ").strip()

    elif auth_type == "api_key":
        console.print("[hash.dim]  Header name (default: X-API-Key):[/hash.dim]")
        api_key_header = console.prompt_raw("  header › ").strip() or "X-API-Key"
        console.print("[hash.dim]  API key value (masked):[/hash.dim]")
        api_key = _read_secret(console, "  key › ").strip()

    elif auth_type == "oauth2_token":
        console.print("[hash.dim]  Access token (masked):[/hash.dim]")
        token = _read_secret(console, "  access_token › ").strip()
        console.print("[hash.dim]  Refresh token (optional, press Enter to skip):[/hash.dim]")
        refresh_tok = _read_secret(console, "  refresh_token › ").strip() or None

    elif auth_type == "oauth2_client":
        console.print("[hash.dim]  Token URL (e.g. https://login.example.com/token):[/hash.dim]")
        tok_url = console.prompt_raw("  token_url › ").strip()
        console.print("[hash.dim]  Client ID:[/hash.dim]")
        client_id = console.prompt_raw("  client_id › ").strip()
        console.print("[hash.dim]  Client secret (masked, optional):[/hash.dim]")
        client_secret = _read_secret(console, "  client_secret › ").strip() or None
        console.print("[hash.dim]  Scope (optional, e.g. read write):[/hash.dim]")
        scope = console.prompt_raw("  scope › ").strip() or ""

    elif auth_type == "oauth2_pkce":
        console.print("[hash.dim]  Authorization URL (e.g. https://auth.example.com/authorize):[/hash.dim]")
        auth_url = console.prompt_raw("  auth_url › ").strip()
        console.print("[hash.dim]  Token URL:[/hash.dim]")
        tok_url = console.prompt_raw("  token_url › ").strip()
        console.print("[hash.dim]  Client ID:[/hash.dim]")
        client_id = console.prompt_raw("  client_id › ").strip()
        console.print("[hash.dim]  Scope (optional):[/hash.dim]")
        scope = console.prompt_raw("  scope › ").strip() or ""

    # ── Step 4: Scope ────────────────────────────────────────────────────
    console.print("\n[hash.dim]  Step 4/5 — Config scope:[/hash.dim]")
    console.print("  [hash.accent]1[/hash.accent]  global  — available in all projects (~/.hash-cli/mcp.json)")
    console.print("  [hash.accent]2[/hash.accent]  project — this project only (.hash-cli/mcp.json in cwd)")
    scope_choice = console.prompt_raw("  scope [1/2] › ").strip()
    scope_level = "project" if scope_choice == "2" else "global"

    # ── Step 5: Save + test ───────────────────────────────────────────────
    console.print("\n[hash.dim]  Step 5/5 — Saving and testing connection…[/hash.dim]")

    ok, msg = add_server(
        name=name,
        url=url,
        command=command,
        args=args_list,
        env=env_dict,
        transport=transport,
        auth_type=auth_type,
        token=token,
        refresh_token=refresh_tok,
        api_key=api_key,
        api_key_header=api_key_header or "X-API-Key",
        token_url=tok_url,
        client_id=client_id,
        client_secret=client_secret,
        scope=scope or "",
        authorization_url=auth_url,
        scope_level=scope_level,
        cwd=os.getcwd(),
    )

    if not ok:
        console.print_error(f"Failed to save: {msg}")
        return

    console.print_success(msg)

    # Test connection
    from hash_cli.mcp_manager import get_server
    cfg = get_server(name, cwd=os.getcwd())
    if cfg:
        console.print("[hash.dim]  Testing connection…[/hash.dim]")
        connected, conn_msg, tools = test_connection(cfg)
        if connected:
            console.print_success(f"Connected! {conn_msg}")
            if tools:
                console.print(f"  [hash.dim]Available tools:[/hash.dim]")
                for t in tools[:10]:
                    console.print(f"  [hash.accent]  • {t.get('name','?')}[/hash.accent]  [hash.dim]{t.get('description','')[:60]}[/hash.dim]")
                if len(tools) > 10:
                    console.print(f"  [hash.dim]  … +{len(tools)-10} more[/hash.dim]")
        else:
            console.print_warning(f"Connection test failed: {conn_msg}")
            console.print("[hash.dim]  Server saved — fix URL/auth and run: /mcp test " + name + "[/hash.dim]")

    console.print(f"\n[hash.dim]  Done. Use  /mcp list  to see all servers.[/hash.dim]\n")


def _get_pulled_ollama_models() -> list[str]:
    """Return list of model names currently pulled in Ollama."""
    import json, urllib.request
    try:
        req = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        data = json.loads(req.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def run_model_switcher(console: HashConsole) -> bool:
    """Interactive model selection. Returns True if model was changed."""
    from hash_cli.config import get_all_models, get_active_model_id, get_custom_models
    all_models = get_all_models()
    active_id = get_active_model_id()
    custom_ids = {m["id"] for m in get_custom_models()}

    console.print("")
    console.print("[hash.brand]  ── Select a model ──[/hash.brand]\n")

    pulled = _get_pulled_ollama_models()

    console.print("[hash.success]  FREE  (runs locally via Ollama)[/hash.success]")
    for i, m in enumerate(all_models, start=1):
        if not m["needs_key"]:
            active_tag = " ◀ active" if m["id"] == active_id else ""
            model_base = m["model"].split(":")[0]
            is_pulled = any(model_base in p for p in pulled)
            dl_tag = "" if is_pulled else "  ⚠ not downloaded"
            color = "hash.tool_ok" if is_pulled else "hash.warning"
            console.print(
                f"  [hash.accent]{i:>2}.[/hash.accent] [hash.accent]{m['label']}[/hash.accent]"
                f"[{color}]{active_tag}{dl_tag}[/{color}]"
            )

    console.print("")
    console.print("[hash.warning]  PREMIUM  (API key required)[/hash.warning]")
    for i, m in enumerate(all_models, start=1):
        if m["needs_key"]:
            active_tag = " ◀ active" if m["id"] == active_id else ""
            custom_tag = " [custom]" if m["id"] in custom_ids else ""
            color = "hash.tool_ok" if active_tag else "hash.dim"
            console.print(
                f"  [hash.accent]{i:>2}.[/hash.accent] [hash.accent]{m['label']}[/hash.accent]"
                f"[hash.dim]{custom_tag}[/hash.dim][{color}]{active_tag}[/{color}]"
            )

    console.print("")
    console.print(f"[hash.dim]  Enter a number (1–{len(all_models)}) or press Enter to cancel:[/hash.dim]")

    try:
        choice = console.prompt_raw("  › ").strip()
    except (KeyboardInterrupt, EOFError):
        return False

    if not choice:
        console.print_info("No change.")
        return False

    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(all_models):
            console.print_error(f"Please enter a number between 1 and {len(all_models)}.")
            return False
    except ValueError:
        console.print_error("Please enter a number.")
        return False

    selected = all_models[idx]
    set_active_model(selected["id"])

    if selected["needs_key"]:
        key_env = selected.get("key_env", "")
        import os
        # Keys are stored per key_env (so OpenAI vs DeepSeek don't collide)
        existing = get_api_key_for_env(key_env) or os.environ.get(key_env)
        if existing:
            masked = existing[:7] + "…" + existing[-4:] if len(existing) > 14 else "***"
            console.print(f"[hash.dim]  Stored key for {key_env}: {masked}[/hash.dim]")
            console.print("[hash.dim]  Use stored key? (Enter = yes,  n = enter new key):[/hash.dim]")
            reuse = console.prompt_raw("  › ").strip().lower()
            if reuse == "n":
                existing = None
        if existing:
            os.environ[key_env] = existing
            console.print_success(f"Using stored key for {key_env}.")
        else:
            console.print(
                f"\n  [hash.info]Get your API key →[/hash.info] "
                f"[hash.accent]{selected.get('key_url','')}[/hash.accent]"
            )
            console.print("[hash.dim]  Paste your API key and press Enter:[/hash.dim]")
            key = console.prompt_raw("  key › ").strip()
            if not key:
                console.print_warning("No key entered — cancelled.")
                return False
            save_api_key_for_env(key_env, key)
            os.environ[key_env] = key
            # Verify it saved
            if get_api_key_for_env(key_env):
                console.print_success(f"Key saved for {key_env} (length {len(key)}).")
            else:
                console.print_error("Key did not save — try again.")
                return False
    else:
        # Ollama free model — check if it's actually pulled
        if selected["provider"] == "ollama":
            pulled = _get_pulled_ollama_models()
            model_base = selected["model"].split(":")[0]
            is_pulled = any(model_base in p for p in pulled)
            if not is_pulled:
                console.print(
                    f"[hash.warning]⚠  Model not downloaded yet. Run this first:[/hash.warning]\n"
                    f"  [hash.accent]{selected['pull_cmd']}[/hash.accent]"
                )
                console.print("[hash.dim]  Switch anyway? (y/N):[/hash.dim]")
                try:
                    confirm = console.prompt_raw("  › ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    confirm = "n"
                if confirm != "y":
                    console.print_info("Cancelled. Model not changed.")
                    return False

    console.print_success(f"Switched to: {selected['model']}")
    return True


# ---------------------------------------------------------------------------
# Slash-command dispatcher
# ---------------------------------------------------------------------------

class SlashCommands:
    EXIT_CMDS = {"/quit", "/exit", "/q", "exit", "quit", "q"}

    def __init__(self, console: HashConsole, session: "Session") -> None:
        self.console = console
        self.session = session

    def handle(self, text: str) -> bool:
        cmd_raw = text.strip()
        cmd = cmd_raw.lower().split()[0] if cmd_raw else ""

        if cmd_raw.lower() in self.EXIT_CMDS:
            self.session.running = False
            return True

        if cmd == "/help":
            self.console.print_help()
            return True

        if cmd == "/clear":
            self.console.clear()
            return True

        if cmd == "/history":
            turns = sum(1 for m in self.session.history
                        if m.__class__.__name__ == "HumanMessage")
            self.console.print_info(f"{turns} turn(s) in current session.")
            return True

        if cmd == "/tools":
            self.console.print_tools(ALL_TOOLS)
            return True

        if cmd == "/model":
            prev_provider = get_active_model_info().get("provider")
            changed = run_model_switcher(self.console)
            if changed:
                info = get_active_model_info()
                new_provider = info.get("provider")

                # ── Manage Ollama lifecycle on provider change ──────────
                from hash_cli.ollama_launcher import (
                    ensure_ollama_running, stop_ollama, is_ollama_running
                )
                if prev_provider == "ollama" and new_provider != "ollama":
                    # Switched away from local → stop Ollama
                    self.console.print_info("Switched to a cloud model — stopping Ollama…")
                    stop_ollama()
                    if not is_ollama_running():
                        self.console.print_success("Ollama stopped.")
                elif prev_provider != "ollama" and new_provider == "ollama":
                    # Switched to local → make sure Ollama is running
                    if not is_ollama_running():
                        self.console.print_info("Switched to a local model — starting Ollama…")
                        ensure_ollama_running()

                self.session.switch_model_from_config()
                self.console.print_success(
                    f"Agent reloaded with model: {info['model']} ({info['provider']})"
                )
            return True

        if cmd == "/setup":
            # Re-run the first-time setup (install Ollama, pull a model)
            from hash_cli.bootstrap import run_bootstrap
            try:
                run_bootstrap(self.console)
                self.session.switch_model_from_config()
            except (KeyboardInterrupt, EOFError):
                self.console.print_info("Setup cancelled.")
            return True

        if cmd == "/addmodel" or cmd == "/add-model":
            _add_custom_model(self.console, self.session)
            return True

        if cmd == "/removemodel" or cmd == "/remove-model":
            from hash_cli.config import get_custom_models, remove_custom_model
            customs = get_custom_models()
            if not customs:
                self.console.print_info("No custom models to remove.")
                return True
            self.console.print("\n[hash.brand]  Custom models:[/hash.brand]")
            for i, m in enumerate(customs, 1):
                self.console.print(f"  [hash.accent]{i}[/hash.accent]  {m['model']}  [hash.dim]({m['provider']})[/hash.dim]")
            self.console.print("[hash.dim]  Enter number to remove, or Enter to cancel:[/hash.dim]")
            ch = self.console.prompt_raw("  › ").strip()
            if ch.isdigit() and 1 <= int(ch) <= len(customs):
                m = customs[int(ch) - 1]
                remove_custom_model(m["id"])
                self.console.print_success(f"Removed {m['model']}.")
            return True

        if cmd == "/pull":
            from hash_cli.bootstrap import pull_model, _STARTER_MODELS, _ollama_path
            parts = cmd_raw.split(maxsplit=1)

            # If a model name was given directly, pull it
            if len(parts) >= 2:
                model = parts[1].strip()
            else:
                # Otherwise show a numbered menu of the free models
                self.console.print("\n[hash.brand]  Select a model to download:[/hash.brand]\n")
                for i, (name, size, desc) in enumerate(_STARTER_MODELS, 1):
                    self.console.print(
                        f"  [hash.accent]{i}[/hash.accent]  {name:<20} "
                        f"[hash.dim]({size}) — {desc}[/hash.dim]"
                    )
                self.console.print("  [hash.accent]c[/hash.accent]  cancel")
                self.console.print(
                    f"\n[hash.dim]  Enter a number (1–{len(_STARTER_MODELS)}), "
                    f"or type any Ollama model name:[/hash.dim]"
                )
                choice = self.console.prompt_raw("  › ").strip().lower()
                if not choice or choice == "c":
                    self.console.print_info("Cancelled.")
                    return True
                if choice.isdigit() and 1 <= int(choice) <= len(_STARTER_MODELS):
                    model = _STARTER_MODELS[int(choice) - 1][0]
                else:
                    # Treat whatever they typed as a model name
                    model = choice

            # Make sure Ollama is available before pulling
            if not _ollama_path():
                self.console.print_warning(
                    "Ollama is not installed. Run /setup first to install it."
                )
                return True
            if not is_ollama_running():
                ensure_ollama_running()

            if pull_model(model, self.console):
                # Offer to make it active
                from hash_cli.config import set_active_model
                set_active_model(f"ollama/{model}")
                self.session.switch_model_from_config()
                self.console.print_success(f"{model} is now the active model.")
            return True

        if cmd == "/key" or cmd == "/keys":
            parts = cmd_raw.split(maxsplit=2)
            sub = parts[1].lower() if len(parts) > 1 else "list"

            if sub == "list":
                stored = list_stored_keys()
                self.console.print("\n[hash.brand]  Stored API keys[/hash.brand]")
                self.console.print(f"[hash.dim]  (config: ~/.hash-cli/config.json)[/hash.dim]\n")
                # Show all premium providers and their key status
                seen_envs = set()
                for m in MODELS:
                    if m.get("needs_key") and m.get("key_env"):
                        env = m["key_env"]
                        if env in seen_envs:
                            continue
                        seen_envs.add(env)
                        masked = stored.get(env)
                        if masked:
                            self.console.print(f"  [hash.success]✓[/hash.success] {env:<20} {masked}")
                        else:
                            self.console.print(f"  [hash.dim]✗ {env:<20} (not set)[/hash.dim]")
                self.console.print(
                    "\n[hash.dim]  /key set <ENV_NAME>     add or replace a key\n"
                    "  /key remove <ENV_NAME>  delete a key[/hash.dim]\n"
                )
                return True

            elif sub == "set":
                env_name = parts[2].strip() if len(parts) > 2 else ""
                if not env_name:
                    self.console.print_warning(
                        "Usage: /key set <ENV_NAME>\n"
                        "  e.g. /key set OPENAI_API_KEY\n"
                        "       /key set DEEPSEEK_API_KEY\n"
                        "       /key set ANTHROPIC_API_KEY"
                    )
                    return True
                self.console.print(f"[hash.dim]  Paste your key for {env_name} and press Enter:[/hash.dim]")
                key = self.console.prompt_raw("  key › ").strip()
                if key:
                    save_api_key_for_env(env_name, key)
                    self.console.print_success(f"Saved {env_name} (length {len(key)}).")
                    # Rebuild the agent in case the active model needed this key
                    self.session.switch_model_from_config()
                    if not self.session.missing_key():
                        self.console.print_success("Model is ready.")
                else:
                    self.console.print_warning("No key entered.")
                return True

            elif sub == "remove":
                env_name = parts[2].strip() if len(parts) > 2 else ""
                if not env_name:
                    self.console.print_warning("Usage: /key remove <ENV_NAME>")
                    return True
                if remove_api_key_for_env(env_name):
                    self.console.print_success(f"Removed {env_name}.")
                else:
                    self.console.print_warning(f"No stored key for {env_name}.")
                return True

            else:
                self.console.print_warning(
                    "Usage: /key [list | set <ENV_NAME> | remove <ENV_NAME>]"
                )
                return True

        if cmd == "/mcp":
            parts = cmd_raw.split(maxsplit=2)
            sub = parts[1].lower() if len(parts) > 1 else "list"

            if sub == "new":
                _mcp_guided_setup(self.console)
                return True

            elif sub == "list":
                from hash_cli.mcp_manager import list_servers, export_kiro_format
                import os
                servers = list_servers(cwd=os.getcwd())
                if not servers:
                    self.console.print_info(
                        "No MCP servers configured. Ask the agent:\n"
                        '  "connect to mcp server named celonis with url https://... bearer token abc123"'
                    )
                else:
                    self.console.print(
                        f"\n[hash.brand]  MCP Servers ({len(servers)}):[/hash.brand]"
                    )
                    for s in servers:
                        status = "[hash.error]disabled[/hash.error]" if s.get("disabled") else "[hash.success]enabled[/hash.success]"
                        kind = s.get("type", "?")
                        loc = s.get("url", s.get("command", ""))[:55]
                        self.console.print(
                            f"  [hash.accent]{s['name']:<20}[/hash.accent] "
                            f"{status}  {kind}  auth={s.get('auth','none')}  {loc}"
                        )
                return True

            elif sub == "json":
                from hash_cli.mcp_manager import export_kiro_format
                import os
                self.console.print(export_kiro_format(cwd=os.getcwd()))
                return True

            elif sub == "test":
                server_name = parts[2].strip() if len(parts) > 2 else ""
                if not server_name:
                    self.console.print_warning("Usage: /mcp test <server-name>")
                    return True
                from hash_cli.mcp_manager import get_server
                from hash_cli.mcp_client import test_connection
                import os
                cfg = get_server(server_name, cwd=os.getcwd())
                if not cfg:
                    self.console.print_error(f"Server '{server_name}' not found.")
                else:
                    ok, msg, tools = test_connection(cfg)
                    if ok:
                        self.console.print_success(f"{server_name}: {msg}")
                        for t in tools[:8]:
                            self.console.print(f"  [hash.dim]  • {t.get('name', '?')}[/hash.dim]")
                        if len(tools) > 8:
                            self.console.print(f"  [hash.dim]  … +{len(tools)-8} more[/hash.dim]")
                    else:
                        self.console.print_error(f"{server_name}: {msg}")
                return True

            elif sub == "remove":
                server_name = parts[2].strip() if len(parts) > 2 else ""
                if not server_name:
                    self.console.print_warning("Usage: /mcp remove <server-name>")
                    return True
                from hash_cli.mcp_manager import remove_server
                import os
                ok, msg = remove_server(server_name, cwd=os.getcwd())
                if ok:
                    self.console.print_success(msg)
                else:
                    self.console.print_error(msg)
                return True

            else:
                self.console.print_warning(
                    "Usage: /mcp [list|json|test <name>|remove <name>]\n"
                    "  /mcp list          — show all configured servers\n"
                    "  /mcp json          — show raw mcp.json content\n"
                    "  /mcp test <name>   — test a server connection\n"
                    "  /mcp remove <name> — remove a server\n"
                    '\nOr ask the agent: "connect to mcp server celonis with url ... bearer token ..."'
                )
                return True

        if cmd == "/memory":
            from hash_cli.memory import format_memory_for_display, clear_memory_category
            parts = cmd_raw.split(maxsplit=1)
            if len(parts) > 1 and parts[1].startswith("clear"):
                cat = parts[1].split(maxsplit=1)[1] if len(parts[1].split()) > 1 else "all"
                self.console.print_success(clear_memory_category(cat))
            else:
                from hash_cli.memory import format_memory_for_display
                self.console.print(f"[hash.brand]  Memory:[/hash.brand]\n{format_memory_for_display()}")
            return True

        if cmd == "/cwd":
            parts = cmd_raw.split(maxsplit=1)
            if len(parts) < 2:
                self.console.print_info(f"Current working directory: {self.session.cwd}")
            else:
                new_cwd = Path(parts[1].strip()).expanduser().resolve()
                if not new_cwd.is_dir():
                    self.console.print_error(f"Directory not found: {new_cwd}")
                else:
                    self.session.switch_cwd(str(new_cwd))
                    self.console.print_success(f"Changed directory to: {new_cwd}")
            return True

        if cmd.startswith("/"):
            self.console.print_warning(f"Unknown command: {cmd}  (type /help for a list)")
            return True

        return False


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class Session:
    def __init__(self, cwd: str, stream: bool) -> None:
        self.cwd = cwd
        self.stream = stream
        self.running = True
        self.history: list[BaseMessage] = []
        self.graph = None
        self.build_error: str | None = None
        # Token tracking (premium models only)
        self.session_input_tokens  = 0
        self.session_output_tokens = 0
        self.session_total_tokens  = 0
        self._build_agent()

    def _build_agent(self) -> None:
        """Build the agent. On failure (e.g. missing API key), store the error
        instead of crashing so the user can fix it from inside the session."""
        apply_api_keys_to_env()
        try:
            config = AgentConfig.from_active_config(cwd=self.cwd)
            self.graph = create_agent(config)
            self.build_error = None
        except Exception as exc:
            self.graph = None
            msg = str(exc)
            if "credentials" in msg.lower() or "api_key" in msg.lower() or "api key" in msg.lower():
                info = get_active_model_info()
                env = info.get("key_env", "API key")
                self.build_error = (
                    f"Missing API key for {info['model']}.\n"
                    f"   Set it with:  /key set {env}\n"
                    f"   Or switch to a free local model with:  /model"
                )
            else:
                self.build_error = f"Could not initialise model: {msg}"

    def missing_key(self) -> bool:
        return self.graph is None

    def switch_model_from_config(self) -> None:
        self.session_input_tokens  = 0
        self.session_output_tokens = 0
        self.session_total_tokens  = 0
        self._build_agent()

    def switch_cwd(self, new_cwd: str) -> None:
        self.cwd = new_cwd
        os.chdir(new_cwd)
        self._build_agent()

    def run_turn(self, user_input: str, console: HashConsole) -> None:
        import time
        # If the agent couldn't be built (missing key), show guidance instead of crashing
        if self.graph is None:
            console.print_error(self.build_error or "Model not ready.")
            return
        info = get_active_model_info()
        is_premium = info.get("needs_key", False)

        t_start = time.perf_counter()

        if self.stream:
            events = stream_agent_realtime(self.graph, user_input, self.history)
            self.history, usage = console.stream_response_rt(events)
            elapsed = time.perf_counter() - t_start
            if usage and is_premium:
                self.session_input_tokens  += usage.input_tokens
                self.session_output_tokens += usage.output_tokens
                self.session_total_tokens  += usage.total_tokens
                console.print_turn_stats(elapsed, usage, self.session_total_tokens)
            else:
                console.print_turn_stats(elapsed)
        else:
            with console._console.status(
                "  Thinking…", spinner="dots", spinner_style="hash.accent",
            ):
                reply, self.history = run_agent(self.graph, user_input, self.history)
            elapsed = time.perf_counter() - t_start
            if reply:
                console._print_assistant(reply)
            console.print_turn_stats(elapsed)


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

@app.command()
def main(
    prompt: Optional[str] = typer.Argument(None),
    cwd: Optional[str] = typer.Option(None, "--cwd", "-C"),
    stream: bool = typer.Option(True, "--stream/--no-stream"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    """Start an interactive hash-cli session or run a one-shot prompt."""

    working_dir = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd())
    if not Path(working_dir).is_dir():
        typer.echo(f"Error: directory not found: {working_dir}", err=True)
        raise typer.Exit(1)
    os.chdir(working_dir)

    # Apply any stored API keys to env before anything else
    apply_api_keys_to_env()

    console = HashConsole(quiet=quiet)
    info = get_active_model_info()

    # ── Ollama auto-launch (only for ollama provider) ──────────────────
    ollama_status = ""
    if info["provider"] == "ollama":
        if is_ollama_running():
            ollama_status = "already running ✓"
        else:
            console.print_info("Ollama not detected — launching in a new terminal…")
            ok = ensure_ollama_running()
            ollama_status = "started ✓" if ok else "could not start (run: ollama serve)"

    session = Session(cwd=working_dir, stream=stream)

    if prompt:
        session.run_turn(prompt, console)
        raise typer.Exit(0)

    if not quiet:
        console.print_welcome(
            model=info["model"],
            provider=info["provider"],
            cwd=working_dir,
            ollama_status=ollama_status,
        )

    console.setup_input()

    # First-run setup — install Ollama + pull a starter model
    from hash_cli.bootstrap import is_first_run, run_bootstrap
    if is_first_run():
        try:
            run_bootstrap(console)
            session.switch_model_from_config()
        except (KeyboardInterrupt, EOFError):
            console.print_info("Setup skipped.")
        except Exception as exc:
            console.print_warning(f"Setup had an issue: {exc}")

    slash = SlashCommands(console, session)

    # If the agent couldn't build (e.g. premium model with no key), warn now
    if session.missing_key() and session.build_error:
        console.print("")
        for i, ln in enumerate(session.build_error.split("\n")):
            prefix = "[hash.warning]⚠  " if i == 0 else "[hash.dim]"
            suffix = "[/hash.warning]" if i == 0 else "[/hash.dim]"
            console.print(f"{prefix}{ln}{suffix}")

    while session.running:
        try:
            user_input = console.prompt()
        except KeyboardInterrupt:
            console.print("")
            console.print_info("Ctrl+C — type 'exit' or Ctrl+D to quit.")
            continue
        except EOFError:
            break

        text = user_input.strip()
        if not text:
            continue

        if slash.handle(text):
            continue

        try:
            session.run_turn(text, console)
        except KeyboardInterrupt:
            console.print("")
            console.print_warning("Turn interrupted.")
        except Exception as exc:
            console.print_error(f"Unexpected error: {exc}")
            if os.getenv("HASH_DEBUG"):
                import traceback
                traceback.print_exc()

        console.print("")

    console.print_rule()
    console.print_info("Session ended. Goodbye.")

    # ── Save memory snapshot ──────────────────────────────────────────
    try:
        from hash_cli.memory import load_memory, _save as _mem_save
        mem = load_memory()
        _mem_save(mem)   # refreshes updated_at timestamp
    except Exception:
        pass

    # ── Stop Ollama if we started it and provider is ollama ───────────
    active = get_active_model_info()
    if active["provider"] == "ollama":
        import platform as _plat
        console.print_info("Stopping Ollama…")
        stop_ollama()
        if is_ollama_running():
            if _plat.system() == "Windows":
                console.print_warning(
                    "Ollama is still running as a background app.\n"
                    "   On Windows it auto-starts and lives in the system tray.\n"
                    "   To fully quit it: click the Ollama icon in the tray (bottom-right) → Quit."
                )
            else:
                console.print_warning("Could not fully stop Ollama — close it manually if needed.")
        else:
            console.print_success("Ollama stopped.")


if __name__ == "__main__":
    app()
