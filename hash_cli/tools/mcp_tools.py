"""MCP tools for the hash-cli agent.

Exposes four tools:
  mcp_add_server   — register a new MCP server (with auth)
  mcp_list_servers — list configured servers and their status
  mcp_call_tool    — call a tool on a connected MCP server
  mcp_remove_server — remove a server from config
"""

from __future__ import annotations

import json
from typing import Any, Optional

from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# mcp_add_server
# ---------------------------------------------------------------------------

@tool
def mcp_add_server(
    name: str,
    url: Optional[str] = None,
    command: Optional[str] = None,
    args: Optional[list] = None,
    env: Optional[dict] = None,
    transport: str = "auto",
    auth_type: str = "none",
    # bearer / oauth2_token
    token: Optional[str] = None,
    refresh_token: Optional[str] = None,
    # api_key
    api_key: Optional[str] = None,
    api_key_header: str = "X-API-Key",
    # oauth2_client (machine-to-machine)
    token_url: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    scope: str = "",
    # oauth2_pkce (user browser flow)
    authorization_url: Optional[str] = None,
    # misc
    extra_headers: Optional[dict] = None,
    scope_level: str = "global",
    test_connection: bool = True,
) -> str:
    """Register a new MCP server with full transport and auth support.

    Supports all MCP transports and auth types. Config is saved to
    ~/.hash-cli/mcp.json (Kiro IDE compatible format).

    TRANSPORT (choose one):
        url      — HTTP server (Streamable HTTP or SSE, auto-detected)
        command  — stdio server (local process, e.g. uvx, npx, python -m)

    TRANSPORT VARIANTS (url only):
        transport="auto"            — try Streamable HTTP first, fall back to SSE
        transport="streamable-http" — force modern protocol (2025-03-26+)
        transport="sse"             — force legacy SSE protocol (2024-11-05)

    AUTH TYPES:
        auth_type="none"           — no authentication
        auth_type="bearer"         — static Bearer token
                                     requires: token
        auth_type="api_key"        — custom header API key
                                     requires: api_key
                                     optional: api_key_header (default "X-API-Key")
        auth_type="oauth2_token"   — pre-obtained OAuth2 access token
                                     requires: token
                                     optional: refresh_token
        auth_type="oauth2_client"  — OAuth2 Client Credentials (machine-to-machine)
                                     requires: token_url, client_id
                                     optional: client_secret, scope
        auth_type="oauth2_pkce"    — OAuth2 Authorization Code + PKCE (opens browser)
                                     requires: authorization_url, token_url, client_id
                                     optional: scope

    EXAMPLES:

        # Celonis (Bearer token, Kiro IDE style)
        mcp_add_server(
            name="celonis",
            url="https://tenant.celonis.cloud/.../mcp/abc123",
            auth_type="bearer",
            token="<your-bearer-token>"
        )

        # Stripe via stdio (uvx)
        mcp_add_server(
            name="stripe",
            command="uvx",
            args=["@stripe/agent-toolkit@latest"],
            env={"STRIPE_SECRET_KEY": "sk_test_..."}
        )

        # GitHub API key
        mcp_add_server(
            name="github",
            url="https://api.githubcopilot.com/mcp/",
            auth_type="api_key",
            api_key="ghp_xxxx",
            api_key_header="Authorization",
        )

        # Salesforce OAuth2 Client Credentials
        mcp_add_server(
            name="salesforce",
            url="https://myorg.salesforce.com/services/mcp",
            auth_type="oauth2_client",
            token_url="https://login.salesforce.com/services/oauth2/token",
            client_id="3MVG9...",
            client_secret="xxx",
            scope="api"
        )

        # Custom server with OAuth2 PKCE (opens browser for user login)
        mcp_add_server(
            name="myserver",
            url="https://api.example.com/mcp",
            auth_type="oauth2_pkce",
            authorization_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
            client_id="my-client-id",
            scope="mcp:read mcp:write"
        )
    """
    from hash_cli.mcp_manager import add_server
    from hash_cli.mcp_client import test_connection as _test, get_client, MCPError
    import os

    ok, msg = add_server(
        name=name,
        url=url,
        command=command,
        args=args,
        env=env,
        transport=transport,
        auth_type=auth_type,
        token=token,
        refresh_token=refresh_token,
        api_key=api_key,
        api_key_header=api_key_header,
        token_url=token_url,
        client_id=client_id,
        client_secret=client_secret,
        scope=scope,
        authorization_url=authorization_url,
        extra_headers=extra_headers or {},
        scope_level=scope_level,
        cwd=os.getcwd(),
    )

    if not ok:
        return f"Error: {msg}"

    if not test_connection:
        return f"+ {msg}"

    from hash_cli.mcp_manager import get_server
    config = get_server(name, cwd=os.getcwd())
    if not config:
        return f"+ {msg}"

    connected, conn_msg, tools = _test(config)
    if connected:
        names = [t.get("name", "?") for t in tools[:10]]
        tools_str = ", ".join(names)
        if len(tools) > 10:
            tools_str += f" (+{len(tools)-10} more)"
        return f"+ {msg}\n+ Connection verified: {conn_msg}\n  Tools: {tools_str}"
    else:
        return (
            f"+ {msg}\n"
            f"! Connection test failed: {conn_msg}\n"
            f"  Server saved -- check URL/auth and retry with /mcp test {name}"
        )


# ---------------------------------------------------------------------------
# mcp_list_servers
# ---------------------------------------------------------------------------

@tool
def mcp_list_servers(test_connections: bool = False) -> str:
    """List all configured MCP servers.

    Args:
        test_connections: If True, ping each server and show live status.
                          Slower but shows which servers are actually reachable.

    Returns:
        Formatted list of servers with type, auth, and status.
    """
    import os
    from hash_cli.mcp_manager import list_servers, get_server
    from hash_cli.mcp_client import test_connection as _test

    servers = list_servers(cwd=os.getcwd())

    if not servers:
        return (
            "No MCP servers configured.\n"
            "Use mcp_add_server() to add one.\n"
            "Config location: ~/.hash-cli/mcp.json"
        )

    lines = [f"Configured MCP servers ({len(servers)}):\n"]
    for s in servers:
        status = "⊘ disabled" if s.get("disabled") else "● enabled"
        kind   = s.get("type", "?")
        auth   = s.get("auth", "none")

        if kind == "http":
            location = s.get("url", "")[:60]
        else:
            location = s.get("command", "") + " " + " ".join(s.get("args", []))
            location = location[:60]

        line = f"  {s['name']:<20} {status:<12} {kind:<6} auth={auth:<12} → {location}"

        if test_connections and not s.get("disabled"):
            cfg = get_server(s["name"], cwd=os.getcwd())
            if cfg:
                ok, _, tools = _test(cfg)
                conn = f"✓ {len(tools)} tools" if ok else "✗ unreachable"
                line += f"  [{conn}]"

        lines.append(line)

    lines.append(f"\nConfig: ~/.hash-cli/mcp.json")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# mcp_call_tool
# ---------------------------------------------------------------------------

@tool
def mcp_call_tool(
    server_name: str,
    tool_name: str,
    arguments: Optional[dict] = None,
) -> str:
    """Call a tool on a configured MCP server.

    Use mcp_list_servers() first to see what servers and tools are available.

    Args:
        server_name: Name of the MCP server (as configured in mcp.json).
        tool_name:   Name of the tool to call on that server.
        arguments:   Tool arguments as a dict. Check the tool's input schema
                     from mcp_list_servers(test_connections=True).

    Returns:
        Tool output as a string, or an error message.

    Example:
        # Call a Celonis tool
        mcp_call_tool(
            server_name="celonis",
            tool_name="get_process_data",
            arguments={"process_id": "abc123"}
        )
    """
    import os
    from hash_cli.mcp_manager import get_server
    from hash_cli.mcp_client import get_client, MCPError

    config = get_server(server_name, cwd=os.getcwd())
    if not config:
        return (
            f"Error: MCP server '{server_name}' not found.\n"
            f"Use mcp_list_servers() to see configured servers."
        )

    if config.get("disabled"):
        return f"Error: MCP server '{server_name}' is disabled. Enable it first."

    client = None
    try:
        client = get_client(config)
        result = client.call_tool(tool_name, arguments or {})
        if isinstance(result, (dict, list)):
            return json.dumps(result, indent=2, ensure_ascii=False)
        return str(result)
    except MCPError as e:
        return f"MCP error calling {server_name}.{tool_name}: {e}"
    except Exception as e:
        return f"Error calling {server_name}.{tool_name}: {e}"
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# mcp_remove_server
# ---------------------------------------------------------------------------

@tool
def mcp_remove_server(name: str) -> str:
    """Remove an MCP server from the configuration.

    Args:
        name: Name of the MCP server to remove.

    Returns:
        Success or error message.
    """
    import os
    from hash_cli.mcp_manager import remove_server
    ok, msg = remove_server(name, cwd=os.getcwd())
    return f"✓ {msg}" if ok else f"Error: {msg}"


# ---------------------------------------------------------------------------
# mcp_get_tools
# ---------------------------------------------------------------------------

@tool
def mcp_get_tools(server_name: str) -> str:
    """List all tools available on a specific MCP server.

    Args:
        server_name: Name of the MCP server to inspect.

    Returns:
        Formatted list of tools with their descriptions and input schemas.
    """
    import os
    from hash_cli.mcp_manager import get_server
    from hash_cli.mcp_client import get_client, MCPError

    config = get_server(server_name, cwd=os.getcwd())
    if not config:
        return f"Error: MCP server '{server_name}' not found."

    if config.get("disabled"):
        return f"Error: MCP server '{server_name}' is disabled."

    client = None
    try:
        client = get_client(config)
        tools = client.list_tools()

        if not tools:
            return f"No tools found on '{server_name}'."

        lines = [f"Tools available on '{server_name}' ({len(tools)}):\n"]
        for t in tools:
            tname = t.get("name", "?")
            desc  = t.get("description", "No description")
            schema = t.get("inputSchema", {})
            props  = schema.get("properties", {})
            required = schema.get("required", [])

            params = []
            for pname, pdef in props.items():
                ptype = pdef.get("type", "any")
                req   = " (required)" if pname in required else ""
                params.append(f"    {pname}: {ptype}{req}")

            lines.append(f"  {tname}")
            lines.append(f"    {desc}")
            if params:
                lines.append("    Parameters:")
                lines.extend(params)
            lines.append("")

        return "\n".join(lines)

    except MCPError as e:
        return f"Error connecting to '{server_name}': {e}"
    except Exception as e:
        return f"Error: {e}"
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
