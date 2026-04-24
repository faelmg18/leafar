"""Figma OAuth 2.0 authentication and MCP client."""

import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from rich.console import Console

console = Console()

_TOKEN_FILE = Path.home() / ".leafar" / "figma_oauth.json"
_REDIRECT_URI = "http://localhost:7777/callback"
_AUTH_URL = "https://www.figma.com/oauth"
_TOKEN_URL = "https://api.figma.com/v1/oauth/token"
_MCP_URL = "https://mcp.figma.com/mcp"


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------

def _load_tokens() -> dict:
    try:
        if _TOKEN_FILE.exists():
            return json.loads(_TOKEN_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_tokens(data: dict) -> None:
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(json.dumps(data))


def get_access_token() -> Optional[str]:
    """Return a valid access token, refreshing if needed."""
    tokens = _load_tokens()
    if not tokens:
        return None

    # Check expiry (with 60s buffer)
    expires_at = tokens.get("expires_at", 0)
    if time.time() < expires_at - 60:
        return tokens.get("access_token")

    # Try to refresh
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        return None

    from .config import Config
    cfg = Config()
    if not cfg.figma_client_id or not cfg.figma_client_secret:
        return None

    try:
        r = requests.post(_TOKEN_URL, data={
            "client_id": cfg.figma_client_id,
            "client_secret": cfg.figma_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        data["expires_at"] = time.time() + data.get("expires_in", 3600)
        data["refresh_token"] = refresh_token  # preserve refresh token
        _save_tokens(data)
        return data["access_token"]
    except Exception:
        return None


def is_logged_in() -> bool:
    return get_access_token() is not None


def logout() -> None:
    _TOKEN_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# OAuth login flow
# ---------------------------------------------------------------------------

def login(client_id: str, client_secret: str) -> bool:
    """Open browser for OAuth login. Returns True on success."""
    state = secrets.token_urlsafe(16)

    params = {
        "client_id": client_id,
        "redirect_uri": _REDIRECT_URI,
        "scope": "file_content:read",
        "state": state,
        "response_type": "code",
    }
    auth_url = f"{_AUTH_URL}?{urlencode(params)}"

    received: dict = {}
    server_ready = threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass  # silencia logs do servidor

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/callback":
                qs = parse_qs(parsed.query)
                received["code"] = qs.get("code", [None])[0]
                received["state"] = qs.get("state", [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"""
                    <html><body style="font-family:sans-serif;text-align:center;padding:60px">
                    <h2>&#10003; Autenticado com sucesso!</h2>
                    <p>Pode fechar esta aba e voltar ao terminal.</p>
                    </body></html>
                """)
                threading.Thread(target=self.server.shutdown, daemon=True).start()

    httpd = HTTPServer(("localhost", 7777), _Handler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    console.print(f"\n[cyan]Abrindo browser para login no Figma...[/cyan]")
    console.print(f"[dim]Se não abrir automaticamente, acesse:[/dim]")
    console.print(f"[dim]{auth_url}[/dim]\n")
    webbrowser.open(auth_url)

    # Aguarda callback (timeout 120s)
    server_thread.join(timeout=120)

    code = received.get("code")
    got_state = received.get("state")

    if not code or got_state != state:
        console.print("[red]Login cancelado ou expirou.[/red]")
        return False

    # Troca code por tokens
    try:
        r = requests.post(_TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": _REDIRECT_URI,
            "code": code,
            "grant_type": "authorization_code",
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        data["expires_at"] = time.time() + data.get("expires_in", 3600)
        _save_tokens(data)
        console.print("[green]✓ Login realizado com sucesso![/green]")
        return True
    except Exception as e:
        console.print(f"[red]Erro ao trocar código por token: {e}[/red]")
        return False


# ---------------------------------------------------------------------------
# MCP client (Streamable HTTP / JSON-RPC)
# ---------------------------------------------------------------------------

class FigmaMCPClient:
    """Cliente MCP para o servidor remoto do Figma."""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self._session_id: Optional[str] = None
        self._msg_id = 0
        self._initialized = False

    def _headers(self) -> dict:
        h = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _post(self, payload: dict, timeout: int = 30) -> dict:
        r = requests.post(
            _MCP_URL,
            headers=self._headers(),
            json=payload,
            timeout=timeout,
        )
        # Captura session id
        sid = r.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid

        if r.status_code == 204:
            return {}
        r.raise_for_status()

        ct = r.headers.get("Content-Type", "")
        if "text/event-stream" in ct:
            # SSE — pega último evento com dados
            last_data = None
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    last_data = line[5:].strip()
            if last_data and last_data != "[DONE]":
                return json.loads(last_data)
            return {}
        return r.json()

    def initialize(self) -> bool:
        if self._initialized:
            return True
        try:
            resp = self._post({
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "rf-leafar", "version": "0.1.0"},
                },
            })
            if "error" in resp:
                return False
            # Envia initialized notification
            self._post({
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            })
            self._initialized = True
            return True
        except Exception:
            return False

    def list_tools(self) -> list[dict]:
        if not self.initialize():
            return []
        try:
            resp = self._post({
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/list",
                "params": {},
            })
            return resp.get("result", {}).get("tools", [])
        except Exception:
            return []

    def call_tool(self, name: str, arguments: dict, timeout: int = 30) -> Any:
        if not self.initialize():
            return {"error": "Não foi possível inicializar o MCP. Rode 'rf figma-login'."}
        try:
            resp = self._post({
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }, timeout=timeout)
            if "error" in resp:
                return {"error": resp["error"].get("message", str(resp["error"]))}
            result = resp.get("result", {})
            content = result.get("content", [])
            # Extrai texto dos blocos de conteúdo
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            return {"output": "\n".join(texts), "raw": result}
        except Exception as e:
            return {"error": str(e)}


def get_mcp_client() -> Optional[FigmaMCPClient]:
    """Retorna cliente MCP autenticado, ou None se não logado."""
    token = get_access_token()
    if not token:
        return None
    return FigmaMCPClient(token)
