from __future__ import annotations

import asyncio
import json
import os
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROBINHOOD_MCP_URL = "https://agent.robinhood.com/mcp/trading"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"


def default_auth_store_path() -> Path:
    override = os.environ.get("FINANCE_ROBINHOOD_MCP_AUTH_FILE")
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "finance-research" / "robinhood_mcp_auth.json"
    return Path.home() / ".finance-research" / "robinhood_mcp_auth.json"


class JsonTokenStorage:
    """Persist MCP OAuth tokens/client registration outside the repository."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_auth_store_path()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens) -> None:
        payload = self._read()
        payload["tokens"] = tokens.model_dump(mode="json")
        self._write(payload)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info) -> None:
        payload = self._read()
        payload["client_info"] = client_info.model_dump(mode="json")
        self._write(payload)


@dataclass(frozen=True)
class OAuthCallback:
    code: str
    state: str | None
    iss: str | None


class _CallbackServer:
    def __init__(self, redirect_uri: str) -> None:
        parsed = urlparse(redirect_uri)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("redirect_uri must be an http loopback URL")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path or "/"
        self.result: OAuthCallback | None = None
        self.error: str | None = None
        self._ready = threading.Event()
        self._done = threading.Event()
        self._server: HTTPServer | None = None

    def start(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != owner.path:
                    self.send_response(404)
                    self.end_headers()
                    return
                params = parse_qs(parsed.query)
                if "error" in params:
                    owner.error = params["error"][0]
                elif "code" in params:
                    owner.result = OAuthCallback(
                        code=params["code"][0],
                        state=params.get("state", [None])[0],
                        iss=params.get("iss", [None])[0],
                    )
                else:
                    owner.error = "OAuth callback did not contain code or error"
                body = b"Robinhood authorization received. You can close this window."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                owner._done.set()

            def log_message(self, format: str, *args: Any) -> None:
                return

        def run() -> None:
            with HTTPServer((self.host, self.port), Handler) as server:
                self._server = server
                self._ready.set()
                while not self._done.is_set():
                    server.handle_request()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("OAuth callback server failed to start")

    def wait(self, timeout: float = 300.0) -> OAuthCallback:
        if not self._done.wait(timeout=timeout):
            raise TimeoutError("Timed out waiting for Robinhood OAuth callback")
        if self.error:
            raise RuntimeError(f"Robinhood OAuth error: {self.error}")
        if self.result is None:
            raise RuntimeError("Robinhood OAuth callback completed without a result")
        return self.result


class RobinhoodMCPClient:
    """Direct Python client for Robinhood's official Trading MCP server.

    No LLM is involved. The first connection performs browser OAuth; subsequent
    runs reuse and refresh the persisted OAuth credentials.
    """

    def __init__(
        self,
        *,
        server_url: str = ROBINHOOD_MCP_URL,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        auth_store: str | Path | None = None,
    ) -> None:
        self.server_url = server_url
        self.redirect_uri = redirect_uri
        self.storage = JsonTokenStorage(auth_store)

    async def _with_session(self, operation):
        import httpx
        from pydantic import AnyUrl
        from mcp import ClientSession
        from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared.auth import OAuthClientMetadata

        callback_server: _CallbackServer | None = None

        async def handle_redirect(auth_url: str) -> None:
            nonlocal callback_server
            callback_server = _CallbackServer(self.redirect_uri)
            callback_server.start()
            if not webbrowser.open(auth_url):
                print(f"Open this URL to authorize Robinhood:\n{auth_url}", flush=True)

        async def handle_callback() -> AuthorizationCodeResult:
            if callback_server is None:
                raise RuntimeError("OAuth callback requested before redirect handler")
            callback = await asyncio.to_thread(callback_server.wait)
            return AuthorizationCodeResult(
                code=callback.code,
                state=callback.state,
                iss=callback.iss,
            )

        oauth = OAuthClientProvider(
            server_url=self.server_url,
            client_metadata=OAuthClientMetadata(
                client_name="finance-research long_growth_v1",
                redirect_uris=[AnyUrl(self.redirect_uri)],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
            ),
            storage=self.storage,
            redirect_handler=handle_redirect,
            callback_handler=handle_callback,
        )

        async with httpx.AsyncClient(auth=oauth) as http_client:
            async with streamable_http_client(
                self.server_url,
                http_client=http_client,
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await operation(session)

    async def list_tools(self) -> list[dict[str, Any]]:
        async def operation(session):
            result = await session.list_tools()
            return [tool.model_dump(mode="json") for tool in result.tools]

        return await self._with_session(operation)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async def operation(session):
            result = await session.call_tool(name, arguments)
            return result.model_dump(mode="json")

        return await self._with_session(operation)
