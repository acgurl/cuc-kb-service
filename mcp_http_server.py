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

import mcp_server as core

SERVER_NAME = "cuc-kb"
SERVER_VERSION = "0.2.0"
MCP_PATH = "/mcp"

HOST = os.getenv("KB_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("KB_MCP_PORT", "8765").strip() or "8765")
TOKEN = os.getenv("KB_MCP_TOKEN", "").strip()

DEFAULT_HOSTS = ",".join(
    [
        "127.0.0.1",
        "localhost",
        f"127.0.0.1:{PORT}",
        f"localhost:{PORT}",
        "host.docker.internal",
        f"host.docker.internal:{PORT}",
        "172.17.0.1",
        f"172.17.0.1:{PORT}",
        "172.18.0.1",
        f"172.18.0.1:{PORT}",
        "172.19.0.1",
        f"172.19.0.1:{PORT}",
        "172.20.0.1",
        f"172.20.0.1:{PORT}",
    ]
)
ALLOWED_HOSTS = [
    item.strip()
    for item in os.getenv("KB_MCP_ALLOWED_HOSTS", DEFAULT_HOSTS).split(",")
    if item.strip()
]
ALLOWED_ORIGINS = [
    item.strip()
    for item in os.getenv(
        "KB_MCP_ALLOWED_ORIGINS",
        ",".join(
            f"https://{host}"
            for host in ALLOWED_HOSTS
            if not host.startswith(("127.", "localhost"))
            and not host.endswith(f":{PORT}")
        ),
    ).split(",")
    if item.strip()
]

INSTRUCTIONS = (
    "校内制度/FAQ 类问题才调用 kb_search；问候、寒暄、自我介绍、"
    "询问助手能力、闲聊和通用知识问题不要调用任何工具。"
    "短问题可补全为完整问句，但不得改变原意或引入示例话题。"
    "回答必须基于返回原文并标注文档名、条款和页码；"
    "citation.file_url 是可点击的原文链接（带 #page=page_start），回答中一并给出；"
    "no_answer=true 时不要编造。"
    + core.REFERENCE_RULE
)

server = MCPServer(
    SERVER_NAME,
    version=SERVER_VERSION,
    instructions=INSTRUCTIONS,
)


def dump(payload):
    return json.dumps(payload, ensure_ascii=False)


@server.tool(
    name="kb_search",
    description=(
        "检索中国传媒大学校内规章制度与常见问题知识库，返回相关条款、原文和出处。"
        "仅当用户问题属于校内制度、办事流程或校园常见问题（FAQ）时调用；"
        "问候、寒暄、自我介绍、询问你是谁或你能做什么、闲聊和通用知识问题不要调用任何工具，直接回答。"
        "用户问题较短或指代不清时可以补全成完整问句再检索，但只能补全用户已表达的意思，"
        "不得引入示例、知识库内容或无关话题。"
        "若返回 no_answer=true，可改写问题后重试一次；仍无结果则不要基于该知识库作答。"
        "检索结果与用户问题无关时，忽略结果并直接回答用户原问题。"
        "引用时使用 citation 中的 doc_title、doc_no、clause_no、page_start；"
        "citation.file_url 可直接打开原文并跳到 page_start 页，page_count 为文档总页数。"
        + core.REFERENCE_RULE
    ),
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
    description=(
        "读取知识库概况：文档数、知识块数、可用助手名和文档类型。"
        "只返回全库统计，不含本会话或账号的引用记录；不要用它回答引用次数，"
        "也不要据此声称用户从未引用过任何文档。"
        "仅在需要了解知识库覆盖范围或选择 kb_search 过滤条件时调用；"
        "问候、闲聊或询问助手能力时不要调用。"
    ),
)
def kb_meta() -> str:
    return dump(core.tool_meta({}))


@server.tool(
    name="kb_documents",
    description=(
        "列出知识库文档及元数据，可按助手或文档类型过滤。"
        "返回 source_file、页数 pages 和原文链接 file_url。用于确认某份制度是否在库中。"
    ),
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
    description=(
        "按 doc_id 读取文档的原文知识块，用于核对引用或补充上下文。"
        "返回每块的页码和 source_file，以及文档总页数 page_count 和原文链接 file_url。"
        "大文档请限制 limit。若据这些内容作答，末尾「参考文件」清单同样要列出该文档的 "
        "doc_title、source_file 和 file_url。"
    ),
)
def kb_document_chunks(doc_id: str, limit: int = 20) -> str:
    return dump(core.tool_document_chunks({"doc_id": doc_id, "limit": limit}))


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
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
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
