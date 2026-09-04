"""MCP client for hash-cli — supports all official transports and auth types.

Transports (per MCP specification):
  1. stdio           — subprocess stdin/stdout (local tools, uvx, npx, python -m)
  2. HTTP+SSE        — legacy HTTP transport (protocol 2024-11-05, still widely used)
  3. Streamable HTTP — modern HTTP transport (protocol 2025-03-26+)

Auth types:
  - none             — no authentication
  - bearer           — static Bearer token in Authorization header
  - api_key          — arbitrary header: value (e.g. X-API-Key)
  - oauth2_token     — OAuth 2.1 with a pre-obtained access token (bearer)
  - oauth2_client    — OAuth 2.1 Client Credentials flow (machine-to-machine)
  - oauth2_pkce      — OAuth 2.1 Authorization Code + PKCE (user-facing, opens browser)

All clients speak JSON-RPC 2.0.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.parse
import uuid
import webbrowser
from abc import ABC, abstractmethod
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx

_TIMEOUT = 15  # seconds — reduced from 30 to fail fast on unreachable servers


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def _rpc(method: str, params: dict | None = None, req_id: str | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id or str(uuid.uuid4())[:8],
        "method": method,
        "params": params or {},
    }


def _result(response: dict) -> Any:
    if "error" in response:
        e = response["error"]
        raise MCPError(f"MCP error {e.get('code','?')}: {e.get('message','unknown')}")
    return response.get("result", {})


def _extract_text(result: Any) -> str:
    """Normalise MCP tool result to a plain string."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and "content" in result:
        blocks = result["content"]
        if isinstance(blocks, list):
            parts = []
            for b in blocks:
                if isinstance(b, dict):
                    if b.get("type") == "text":
                        parts.append(b.get("text", ""))
                    else:
                        parts.append(json.dumps(b, indent=2))
                else:
                    parts.append(str(b))
            return "\n".join(parts)
        return str(blocks)
    if isinstance(result, (dict, list)):
        return json.dumps(result, indent=2, ensure_ascii=False)
    return str(result)


class MCPError(Exception):
    pass


# ---------------------------------------------------------------------------
# Base client interface
# ---------------------------------------------------------------------------

class MCPClient(ABC):
    """Abstract base for all MCP transport clients."""

    @abstractmethod
    def initialize(self) -> dict:
        ...

    @abstractmethod
    def list_tools(self) -> list[dict]:
        ...

    @abstractmethod
    def call_tool(self, name: str, arguments: dict) -> str:
        ...

    def list_resources(self) -> list[dict]:
        return []

    def list_prompts(self) -> list[dict]:
        return []

    def ping(self) -> bool:
        try:
            self.initialize()
            return True
        except Exception:
            return False

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# OAuth 2.1 helpers
# ---------------------------------------------------------------------------

class OAuth2TokenManager:
    """Manages OAuth 2.1 tokens — fetches, refreshes, and stores them."""

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str | None = None,
        scope: str = "",
        extra_headers: dict | None = None,
    ) -> None:
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.extra_headers = extra_headers or {}
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0

    def set_token(self, access_token: str, refresh_token: str | None = None,
                   expires_in: int = 3600) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._expires_at = time.time() + expires_in - 30  # 30s buffer

    def _is_expired(self) -> bool:
        return self._expires_at > 0 and time.time() >= self._expires_at

    def get_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if self._access_token and not self._is_expired():
            return self._access_token
        if self._refresh_token:
            self._do_refresh()
            return self._access_token or ""
        raise MCPError("No access token available. Run the OAuth flow first.")

    def client_credentials(self) -> None:
        """Fetch a token using OAuth 2.1 Client Credentials grant."""
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        if self.scope:
            data["scope"] = self.scope

        resp = httpx.post(
            self.token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     **self.extra_headers},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        self.set_token(
            body["access_token"],
            body.get("refresh_token"),
            body.get("expires_in", 3600),
        )

    def _do_refresh(self) -> None:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self.client_id,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        try:
            resp = httpx.post(
                self.token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         **self.extra_headers},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            body = resp.json()
            self.set_token(
                body["access_token"],
                body.get("refresh_token", self._refresh_token),
                body.get("expires_in", 3600),
            )
        except Exception as e:
            raise MCPError(f"Token refresh failed: {e}")


def pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def run_oauth2_pkce_flow(
    auth_url: str,
    token_url: str,
    client_id: str,
    scope: str = "",
    redirect_port: int = 9876,
) -> tuple[str, str | None, int]:
    """Run the OAuth 2.1 Authorization Code + PKCE flow.

    Opens the browser, waits for the callback on localhost, exchanges the
    code for tokens.

    Returns (access_token, refresh_token, expires_in).
    """
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://localhost:{redirect_port}/callback"

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    full_url = auth_url + "?" + urllib.parse.urlencode(params)

    # Capture the callback
    auth_code: list[str] = []
    returned_state: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            auth_code.append(qs.get("code", [""])[0])
            returned_state.append(qs.get("state", [""])[0])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>hash-cli: Authorization complete. "
                b"You can close this window.</h2></body></html>"
            )

        def log_message(self, *args):
            pass  # suppress server logs

    server = HTTPServer(("localhost", redirect_port), _Handler)
    server.timeout = 120  # wait up to 2 minutes

    print(f"\nOpening browser for OAuth2 authorization...")
    webbrowser.open(full_url)
    print(f"Waiting for callback on http://localhost:{redirect_port}/callback ...")

    server.handle_request()
    server.server_close()

    if not auth_code or not auth_code[0]:
        raise MCPError("OAuth2 flow failed: no authorization code received.")
    if returned_state and returned_state[0] != state:
        raise MCPError("OAuth2 flow failed: state mismatch (possible CSRF).")

    # Exchange code for tokens
    resp = httpx.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "code": auth_code[0],
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    return (
        body["access_token"],
        body.get("refresh_token"),
        body.get("expires_in", 3600),
    )


# ---------------------------------------------------------------------------
# 1. Stdio transport
# ---------------------------------------------------------------------------

class StdioMCPClient(MCPClient):
    """MCP client for stdio-based servers (local processes).

    Config keys: command, args, env
    Auth: credentials passed via env vars (not HTTP headers)
    """

    def __init__(self, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None) -> None:
        full_env = {**os.environ, **(env or {})}
        self._proc = subprocess.Popen(
            [command] + (args or []),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=full_env, text=True,
            encoding="utf-8", errors="replace",
        )
        self._lock = threading.Lock()
        self._initialized = False

    def _send(self, payload: dict) -> dict:
        with self._lock:
            self._proc.stdin.write(json.dumps(payload) + "\n")  # type: ignore
            self._proc.stdin.flush()                             # type: ignore
            line = self._proc.stdout.readline()                  # type: ignore
            if not line:
                raise MCPError("Stdio server closed unexpectedly.")
            return json.loads(line)

    def initialize(self) -> dict:
        r = _result(self._send(_rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "hash-cli", "version": "0.1.0"},
        })))
        # Send initialized notification (required by spec)
        self._proc.stdin.write(json.dumps({  # type: ignore
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {}
        }) + "\n")
        self._proc.stdin.flush()  # type: ignore
        self._initialized = True
        return r

    def list_tools(self) -> list[dict]:
        if not self._initialized:
            self.initialize()
        return _result(self._send(_rpc("tools/list"))).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        if not self._initialized:
            self.initialize()
        r = _result(self._send(_rpc("tools/call", {"name": name, "arguments": arguments})))
        return _extract_text(r)

    def close(self) -> None:
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 2. HTTP+SSE transport (legacy — protocol 2024-11-05)
# ---------------------------------------------------------------------------

class SSEMCPClient(MCPClient):
    """MCP client for HTTP+SSE servers (legacy protocol 2024-11-05).

    The server sends an SSE endpoint event; the client POSTs to that endpoint.
    Used by: Celonis, many currently deployed MCP servers.

    Config keys: url, headers
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None,
                 oauth: OAuth2TokenManager | None = None) -> None:
        self._base_url = url.rstrip("/")
        self._static_headers = headers or {}
        self._oauth = oauth
        self._post_url: str | None = None
        self._initialized = False
        self._client = httpx.Client(follow_redirects=True, timeout=_TIMEOUT)

    def _auth_headers(self) -> dict[str, str]:
        h = dict(self._static_headers)
        if self._oauth:
            h["Authorization"] = f"Bearer {self._oauth.get_token()}"
        return h

    def _discover_post_url(self) -> None:
        """Connect to the SSE stream to discover the POST endpoint."""
        headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            **self._auth_headers(),
        }
        sse_url = self._base_url if self._base_url.endswith("/sse") else self._base_url + "/sse"

        try:
            with self._client.stream("GET", sse_url, headers=headers) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data.startswith("http"):
                            self._post_url = data
                            return
        except Exception:
            pass

        # Fallback: many servers accept POST at the base URL directly
        self._post_url = self._base_url

    def _post(self, payload: dict) -> dict:
        if not self._post_url:
            self._discover_post_url()

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._auth_headers(),
        }
        resp = self._client.post(self._post_url, json=payload, headers=headers)  # type: ignore
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if "text/event-stream" in ct:
            # Parse SSE response
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
        return resp.json()

    def initialize(self) -> dict:
        r = _result(self._post(_rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}, "resources": {}},
            "clientInfo": {"name": "hash-cli", "version": "0.1.0"},
        })))
        self._initialized = True
        return r

    def list_tools(self) -> list[dict]:
        if not self._initialized:
            self.initialize()
        return _result(self._post(_rpc("tools/list"))).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        if not self._initialized:
            self.initialize()
        r = _result(self._post(_rpc("tools/call", {"name": name, "arguments": arguments})))
        return _extract_text(r)

    def list_resources(self) -> list[dict]:
        if not self._initialized:
            self.initialize()
        try:
            return _result(self._post(_rpc("resources/list"))).get("resources", [])
        except MCPError:
            return []

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------------
# 3. Streamable HTTP transport (current — protocol 2025-03-26+)
# ---------------------------------------------------------------------------

class StreamableHTTPMCPClient(MCPClient):
    """MCP client for Streamable HTTP servers (modern protocol 2025-03-26+).

    Uses HTTP POST for all requests. Server may respond with JSON or SSE stream.
    Supports optional session management via Mcp-Session-Id header.

    Config keys: url, headers
    Auth: bearer, api_key, oauth2
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None,
                 oauth: OAuth2TokenManager | None = None) -> None:
        self._url = url.rstrip("/")
        self._static_headers = headers or {}
        self._oauth = oauth
        self._session_id: str | None = None
        self._initialized = False
        self._client = httpx.Client(follow_redirects=True, timeout=_TIMEOUT)

    def _auth_headers(self) -> dict[str, str]:
        h = dict(self._static_headers)
        if self._oauth:
            h["Authorization"] = f"Bearer {self._oauth.get_token()}"
        return h

    def _post(self, payload: dict) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-03-26",
            **self._auth_headers(),
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        resp = self._client.post(self._url, json=payload, headers=headers)

        # Capture session ID if server sends one
        if "Mcp-Session-Id" in resp.headers:
            self._session_id = resp.headers["Mcp-Session-Id"]

        if resp.status_code == 401:
            raise MCPError("HTTP 401 Unauthorized. Check your auth credentials.")
        resp.raise_for_status()

        ct = resp.headers.get("content-type", "")
        if "text/event-stream" in ct:
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
            raise MCPError("SSE response contained no data.")
        return resp.json()

    def initialize(self) -> dict:
        r = _result(self._post(_rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "clientInfo": {"name": "hash-cli", "version": "0.1.0"},
        })))
        # Send initialized notification
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        except Exception:
            pass  # notification — ignore errors
        self._initialized = True
        return r

    def list_tools(self) -> list[dict]:
        if not self._initialized:
            self.initialize()
        return _result(self._post(_rpc("tools/list"))).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        if not self._initialized:
            self.initialize()
        r = _result(self._post(_rpc("tools/call", {"name": name, "arguments": arguments})))
        return _extract_text(r)

    def list_resources(self) -> list[dict]:
        if not self._initialized:
            self.initialize()
        try:
            return _result(self._post(_rpc("resources/list"))).get("resources", [])
        except MCPError:
            return []

    def list_prompts(self) -> list[dict]:
        if not self._initialized:
            self.initialize()
        try:
            return _result(self._post(_rpc("prompts/list"))).get("prompts", [])
        except MCPError:
            return []

    def close(self) -> None:
        # Send terminate session if we have a session ID
        if self._session_id:
            try:
                headers = {
                    "Mcp-Session-Id": self._session_id,
                    **self._auth_headers(),
                }
                self._client.delete(self._url, headers=headers, timeout=5)
            except Exception:
                pass
        self._client.close()


# ---------------------------------------------------------------------------
# Auto-detecting HTTP client (tries Streamable HTTP first, falls back to SSE)
# ---------------------------------------------------------------------------

class AutoHTTPMCPClient(MCPClient):
    """Tries Streamable HTTP (2025-03-26) first, falls back to SSE (2024-11-05).

    This is what most users should use for URL-based servers — it handles
    both old and new server implementations automatically.
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None,
                 oauth: OAuth2TokenManager | None = None) -> None:
        self._url = url
        self._headers = headers
        self._oauth = oauth
        self._client: MCPClient | None = None

    def _get_client(self) -> MCPClient:
        if self._client:
            return self._client
        # Try Streamable HTTP first
        c = StreamableHTTPMCPClient(self._url, self._headers, self._oauth)
        try:
            c.initialize()
            self._client = c
            return self._client
        except Exception:
            try:
                c.close()
            except Exception:
                pass
        # Fall back to SSE
        c2 = SSEMCPClient(self._url, self._headers, self._oauth)
        c2.initialize()
        self._client = c2
        return self._client

    def initialize(self) -> dict:
        return self._get_client().initialize()

    def list_tools(self) -> list[dict]:
        return self._get_client().list_tools()

    def call_tool(self, name: str, arguments: dict) -> str:
        return self._get_client().call_tool(name, arguments)

    def list_resources(self) -> list[dict]:
        return self._get_client().list_resources()

    def close(self) -> None:
        if self._client:
            self._client.close()


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def get_client(server_config: dict) -> MCPClient:
    """Build the right MCP client from a server config dict.

    Handles all transport types and auth configurations automatically.
    """
    auth = server_config.get("auth", {})
    auth_type = auth.get("type", "none") if isinstance(auth, dict) else "none"

    # Build OAuth manager if needed
    oauth: OAuth2TokenManager | None = None

    if auth_type in ("oauth2_client", "oauth2_token", "oauth2_pkce"):
        token_url   = auth.get("token_url", "")
        client_id   = auth.get("client_id", "")
        client_secret = auth.get("client_secret")
        scope       = auth.get("scope", "")
        access_token = auth.get("access_token")

        oauth = OAuth2TokenManager(token_url, client_id, client_secret, scope)

        if auth_type == "oauth2_token" and access_token:
            # Pre-obtained token
            oauth.set_token(
                access_token,
                auth.get("refresh_token"),
                auth.get("expires_in", 3600),
            )
        elif auth_type == "oauth2_client":
            # Machine-to-machine: fetch token now
            oauth.client_credentials()
        elif auth_type == "oauth2_pkce":
            # User-facing: run browser flow
            auth_url = auth.get("authorization_url", "")
            if not auth_url:
                raise MCPError("oauth2_pkce requires 'authorization_url' in auth config.")
            access_token, refresh_token, expires_in = run_oauth2_pkce_flow(
                auth_url=auth_url,
                token_url=token_url,
                client_id=client_id,
                scope=scope,
            )
            oauth.set_token(access_token, refresh_token, expires_in)

    # Build static headers (bearer / api_key / custom)
    static_headers = dict(server_config.get("headers", {}))

    # Legacy format: headers dict directly (Kiro IDE style — keep working)
    # New format: auth dict with type field

    if auth_type == "bearer":
        token = auth.get("token", "")
        if token:
            static_headers["Authorization"] = f"Bearer {token}"

    elif auth_type == "api_key":
        key_name  = auth.get("header", "X-API-Key")
        key_value = auth.get("key", "")
        if key_value:
            static_headers[key_name] = key_value

    # Choose transport
    transport = server_config.get("transport", "auto")

    if "command" in server_config:
        return StdioMCPClient(
            command=server_config["command"],
            args=server_config.get("args"),
            env=server_config.get("env"),
        )

    elif "url" in server_config:
        url = server_config["url"]
        if transport == "sse":
            return SSEMCPClient(url, static_headers, oauth)
        elif transport == "streamable-http":
            return StreamableHTTPMCPClient(url, static_headers, oauth)
        else:
            # "auto" — try both, Streamable first
            return AutoHTTPMCPClient(url, static_headers, oauth)

    else:
        raise MCPError("Server config must have 'url' or 'command'.")


# ---------------------------------------------------------------------------
# Connection tester
# ---------------------------------------------------------------------------

def test_connection(server_config: dict) -> tuple[bool, str, list[dict]]:
    """Test a server connection. Returns (ok, message, tools_list)."""
    client = None
    try:
        client = get_client(server_config)
        client.initialize()
        tools = client.list_tools()
        return True, f"Connected — {len(tools)} tool(s) available.", tools
    except MCPError as e:
        return False, str(e), []
    except Exception as e:
        return False, f"Connection failed: {e}", []
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
