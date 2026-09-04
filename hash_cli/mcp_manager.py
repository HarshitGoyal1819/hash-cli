"""MCP (Model Context Protocol) configuration manager for hash-cli.

Manages ~/.hash-cli/mcp.json — compatible with Kiro IDE format.

Full schema (all fields optional except name + one of url/command):
{
  "mcpServers": {
    "<name>": {
      // Transport (choose one):
      "url":       "https://...",           // HTTP server (SSE or Streamable HTTP)
      "command":   "uvx",                   // stdio server executable
      "args":      ["package@latest"],      // stdio server arguments
      "env":       {"KEY": "VALUE"},        // stdio server env vars
      "transport": "auto",                  // "auto"|"sse"|"streamable-http" (url only)

      // Auth (choose one style):
      // Style A — Kiro IDE legacy (headers dict):
      "headers": {"Authorization": "Bearer <token>"},

      // Style B — typed auth block (hash-cli extended):
      "auth": {
        "type": "none|bearer|api_key|oauth2_token|oauth2_client|oauth2_pkce",

        // bearer:
        "token": "<access_token>",

        // api_key:
        "key": "<api_key_value>",
        "header": "X-API-Key",             // header name, default X-API-Key

        // oauth2_token (pre-obtained token):
        "access_token":  "<token>",
        "refresh_token": "<token>",        // optional
        "expires_in":    3600,             // optional

        // oauth2_client (machine-to-machine):
        "token_url":     "https://.../token",
        "client_id":     "...",
        "client_secret": "...",            // optional for public clients
        "scope":         "read write",     // optional

        // oauth2_pkce (user browser flow):
        "authorization_url": "https://.../authorize",
        "token_url":         "https://.../token",
        "client_id":         "...",
        "scope":             "...",
      },

      "disabled": false
    }
  }
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hash_cli.config import CONFIG_DIR

MCP_FILE = CONFIG_DIR / "mcp.json"

# Also look for a project-level mcp.json in .hash-cli/ relative to cwd
_PROJECT_MCP_RELATIVE = ".hash-cli/mcp.json"


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _load_file(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"mcpServers": {}}


def _save_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_mcp_path(cwd: str | None = None) -> Path:
    """Return project-level mcp.json if it exists, else the global one."""
    if cwd:
        project = Path(cwd) / _PROJECT_MCP_RELATIVE
        if project.exists():
            return project
    return MCP_FILE


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_servers(cwd: str | None = None) -> dict[str, Any]:
    """Return the mcpServers dict, merging global + project configs.

    Project-level entries override global ones with the same name.
    """
    global_data  = _load_file(MCP_FILE)
    global_svrs  = global_data.get("mcpServers", {})

    if cwd:
        project_path = Path(cwd) / _PROJECT_MCP_RELATIVE
        if project_path.exists():
            project_data = _load_file(project_path)
            project_svrs = project_data.get("mcpServers", {})
            return {**global_svrs, **project_svrs}

    return global_svrs


def add_server(
    name: str,
    *,
    # Transport
    url: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    transport: str = "auto",
    # Auth
    auth_type: str = "none",
    # bearer / oauth2_token
    token: str | None = None,
    refresh_token: str | None = None,
    expires_in: int = 3600,
    # api_key
    api_key: str | None = None,
    api_key_header: str = "X-API-Key",
    # oauth2_client + oauth2_pkce shared
    token_url: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    scope: str = "",
    # oauth2_pkce only
    authorization_url: str | None = None,
    # legacy Kiro-style headers (always merged in)
    extra_headers: dict[str, str] | None = None,
    disabled: bool = False,
    scope_level: str = "global",    # "global" | "project"
    cwd: str | None = None,
) -> tuple[bool, str]:
    """Add or update an MCP server entry supporting all transport and auth types."""

    if not url and not command:
        return False, "Either 'url' (HTTP server) or 'command' (stdio server) is required."
    if url and command:
        return False, "Provide either 'url' or 'command', not both."

    config: dict[str, Any] = {}

    # Transport
    if url:
        config["url"] = url
        if transport != "auto":
            config["transport"] = transport
    if command:
        config["command"] = command
        if args:
            config["args"] = args
        if env:
            config["env"] = env

    # Auth block
    auth_type = auth_type.lower().strip()
    valid_types = {"none", "bearer", "api_key", "oauth2_token",
                   "oauth2_client", "oauth2_pkce"}
    if auth_type not in valid_types:
        return False, f"Unknown auth_type '{auth_type}'. Valid: {', '.join(sorted(valid_types))}"

    if auth_type != "none":
        auth_block: dict[str, Any] = {"type": auth_type}

        if auth_type == "bearer":
            if not token:
                return False, "auth_type 'bearer' requires a token."
            auth_block["token"] = token

        elif auth_type == "api_key":
            if not api_key:
                return False, "auth_type 'api_key' requires an api_key."
            auth_block["key"] = api_key
            auth_block["header"] = api_key_header

        elif auth_type == "oauth2_token":
            if not token:
                return False, "auth_type 'oauth2_token' requires an access token."
            auth_block["access_token"] = token
            if refresh_token:
                auth_block["refresh_token"] = refresh_token
            auth_block["expires_in"] = expires_in

        elif auth_type == "oauth2_client":
            if not token_url or not client_id:
                return False, "auth_type 'oauth2_client' requires token_url and client_id."
            auth_block["token_url"] = token_url
            auth_block["client_id"] = client_id
            if client_secret:
                auth_block["client_secret"] = client_secret
            if scope:
                auth_block["scope"] = scope

        elif auth_type == "oauth2_pkce":
            if not authorization_url or not token_url or not client_id:
                return False, (
                    "auth_type 'oauth2_pkce' requires "
                    "authorization_url, token_url, and client_id."
                )
            auth_block["authorization_url"] = authorization_url
            auth_block["token_url"] = token_url
            auth_block["client_id"] = client_id
            if scope:
                auth_block["scope"] = scope

        config["auth"] = auth_block

    # Legacy Kiro-style headers (always supported for backward compat)
    # If bearer auth, also write the Kiro-style header so Kiro IDE can use the same file
    headers: dict[str, str] = dict(extra_headers or {})
    if auth_type == "bearer" and token:
        headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "api_key" and api_key:
        headers[api_key_header] = api_key
    if headers:
        config["headers"] = headers

    config["disabled"] = disabled

    # Save
    if scope_level == "project" and cwd:
        path = Path(cwd) / _PROJECT_MCP_RELATIVE
    else:
        path = MCP_FILE

    data = _load_file(path)
    data.setdefault("mcpServers", {})[name] = config
    _save_file(path, data)

    scope_label = f"project ({path})" if scope_level == "project" else "global (~/.hash-cli/mcp.json)"
    return True, f"MCP server '{name}' saved to {scope_label}."


def remove_server(name: str, cwd: str | None = None) -> tuple[bool, str]:
    """Remove an MCP server entry (checks both global and project configs)."""
    removed = False

    for path in _candidate_paths(cwd):
        data = _load_file(path)
        servers = data.get("mcpServers", {})
        if name in servers:
            del servers[name]
            data["mcpServers"] = servers
            _save_file(path, data)
            removed = True

    if removed:
        return True, f"MCP server '{name}' removed."
    return False, f"MCP server '{name}' not found."


def toggle_server(name: str, disabled: bool, cwd: str | None = None) -> tuple[bool, str]:
    """Enable or disable a server without removing it."""
    for path in _candidate_paths(cwd):
        data = _load_file(path)
        servers = data.get("mcpServers", {})
        if name in servers:
            servers[name]["disabled"] = disabled
            _save_file(path, data)
            state = "disabled" if disabled else "enabled"
            return True, f"MCP server '{name}' {state}."
    return False, f"MCP server '{name}' not found."


def get_server(name: str, cwd: str | None = None) -> dict[str, Any] | None:
    """Return config for a single server, or None if not found."""
    return load_servers(cwd).get(name)


def list_servers(cwd: str | None = None) -> list[dict[str, Any]]:
    """Return a list of server summaries for display."""
    servers = load_servers(cwd)
    result = []
    for name, cfg in servers.items():
        entry: dict[str, Any] = {"name": name}
        if "url" in cfg:
            entry["type"] = "http"
            entry["url"] = cfg["url"]
            entry["transport"] = cfg.get("transport", "auto")
        elif "command" in cfg:
            entry["type"] = "stdio"
            entry["command"] = cfg["command"]
            entry["args"] = cfg.get("args", [])

        # Determine auth type from new auth block or legacy headers
        auth_block = cfg.get("auth", {})
        if isinstance(auth_block, dict) and auth_block.get("type"):
            entry["auth"] = auth_block["type"]
        else:
            headers = cfg.get("headers", {})
            if "Authorization" in headers:
                entry["auth"] = "bearer (legacy)"
            elif any(k.lower() in ("x-api-key", "api-key") for k in headers):
                entry["auth"] = "api_key (legacy)"
            elif headers:
                entry["auth"] = "custom headers"
            else:
                entry["auth"] = "none"

        entry["disabled"] = cfg.get("disabled", False)
        result.append(entry)
    return result


def export_kiro_format(cwd: str | None = None) -> str:
    """Return the merged mcp.json as a pretty-printed JSON string (Kiro IDE format)."""
    servers = load_servers(cwd)
    return json.dumps({"mcpServers": servers}, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candidate_paths(cwd: str | None) -> list[Path]:
    paths = [MCP_FILE]
    if cwd:
        project = Path(cwd) / _PROJECT_MCP_RELATIVE
        if project != MCP_FILE:
            paths.insert(0, project)
    return paths
