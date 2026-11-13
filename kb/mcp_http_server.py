#!/usr/bin/env python3
"""Persistent Streamable HTTP MCP endpoint for the CUC KB.

Runs the same four tools as mcp_server.py, but over HTTP so local agents and
Docker containers do not need to spawn a stdio process. The service binds to
KB_MCP_HOST (127.0.0.1 on phoenix). Host processes use 127.0.0.1 directly;
containers use a separate forwarder bound to the Docker bridge gateways
(mcp_gateway_proxy.py, systemd cuc-kb-mcp-gateway), so the endpoint is never
bound to the public interface.

Authentication accepts any of:
  - Authorization: Bearer <KB_MCP_TOKEN>
  - X-MCP-Token: <KB_MCP_TOKEN>
  - ?token=<KB_MCP_TOKEN> on the endpoint URL
"""

import hmac
import json
import os
import sys
from urllib.parse import parse_qs

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from kb import mcp_server as core
from kb.mcp_gateway_proxy import DEFAULT_BIND_HOSTS as DOCKER_GATEWAYS

SERVER_NAME = core.SERVER_NAME
SERVER_VERSION = "0.2.0"
MCP_PATH = "/mcp"

HOST = os.getenv("KB_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("KB_MCP_PORT", "8765").strip() or "8765")
TOKEN = os.getenv("KB_MCP_TOKEN", "").strip()

DEFAULT_HOSTS = ",".join(
    entry
    for host in ("127.0.0.1", "localhost", "host.docker.internal", *DOCKER_GATEWAYS)
    for entry in (host, f"{host}:{PORT}")
)


def env_list(name, default):
    """Comma-separated env list; unset or blank falls back to the default."""
    raw = os.getenv(name, "").strip() or default
    return [item.strip() for item in raw.split(",") if item.strip()]


ALLOWED_HOSTS = env_list("KB_MCP_ALLOWED_HOSTS", DEFAULT_HOSTS)
ALLOWED_ORIGINS = env_list(
    "KB_MCP_ALLOWED_ORIGINS",
    ",".join(
        f"https://{host}"
        for host in ALLOWED_HOSTS
        if not host.startswith(("127.", "localhost")) and not host.endswith(f":{PORT}")
    ),
)

server = MCPServer(
    SERVER_NAME,
    version=SERVER_VERSION,
    instructions=core.INSTRUCTIONS,
)


def dump(payload):
    return json.dumps(payload, ensure_ascii=False)


@server.tool(
    name="kb_search",
    description=core.TOOL_DESCRIPTIONS["kb_search"],
)
def kb_search(
    query: str,
    assistant: str | None = None,
    doc_type: list[str] | None = None,
    top_k: int = 5,
) -> str:
    arguments = {"query": query, "top_k": top_k}
    if assistant:
        arguments["assistant"] = assistant
    if doc_type:
        arguments["doc_type"] = doc_type
    return dump(core.tool_search(arguments))


@server.tool(
    name="kb_meta",
    description=core.TOOL_DESCRIPTIONS["kb_meta"],
)
def kb_meta() -> str:
    return dump(core.tool_meta({}))


@server.tool(
    name="kb_documents",
    description=core.TOOL_DESCRIPTIONS["kb_documents"],
)
def kb_documents(
    assistant: str | None = None,
    doc_type: list[str] | None = None,
) -> str:
    arguments = {}
    if assistant:
        arguments["assistant"] = assistant
    if doc_type:
        arguments["doc_type"] = doc_type
    return dump(core.tool_documents(arguments))


@server.tool(
    name="kb_document_chunks",
    description=core.TOOL_DESCRIPTIONS["kb_document_chunks"],
)
def kb_document_chunks(
    doc_id: str,
    chunk_id: str | None = None,
    before: int = 2,
    after: int = 2,
    page: int | None = None,
    page_end: int | None = None,
    offset: int = 0,
    limit: int = 20,
) -> str:
    arguments = {
        "doc_id": doc_id, "limit": limit, "offset": offset,
        "before": before, "after": after,
    }
    if chunk_id:
        arguments["chunk_id"] = chunk_id
    if page is not None:
        arguments["page"] = page
    if page_end is not None:
        arguments["page_end"] = page_end
    return dump(core.tool_document_chunks(arguments))


class AuthMiddleware:
    """Require the MCP token, with an unauthenticated /healthz."""

    CORS_HEADERS = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Authorization, Content-Type, X-MCP-Token",
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    }

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/healthz":
            await JSONResponse({"status": "ok"}, headers=self.CORS_HEADERS)(
                scope, receive, send
            )
            return
        if scope.get("method") == "OPTIONS":
            await JSONResponse(None, status_code=204, headers=self.CORS_HEADERS)(
                scope, receive, send
            )
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        supplied = ""
        authorization = headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not supplied:
            supplied = headers.get("x-mcp-token", "").strip()
        if not supplied:
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            supplied = (query.get("token") or [""])[0].strip()

        if not TOKEN or not hmac.compare_digest(supplied, TOKEN):
            await JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers=self.CORS_HEADERS,
            )(scope, receive, send)
            return

        await self.app(scope, receive, send)


def build_app():
    inner = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        host=HOST,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=ALLOWED_HOSTS,
            allowed_origins=ALLOWED_ORIGINS,
        ),
    )
    return AuthMiddleware(inner)


app = build_app()


def main():
    if not TOKEN:
        print("KB_MCP_TOKEN is required", file=sys.stderr)
        raise SystemExit(1)
    print(
        f"{SERVER_NAME} MCP HTTP on {HOST}:{PORT}{MCP_PATH} "
        f"(hosts={len(ALLOWED_HOSTS)})",
        flush=True,
    )
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
