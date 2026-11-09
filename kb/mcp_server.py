#!/usr/bin/env python3
"""Stdio MCP server exposing the CUC KB retrieval API. Standard library only."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SERVER_NAME = "cuc-kb"
SERVER_VERSION = "0.1.0"
SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")

BASE_URL = os.getenv("KB_API_URL", "http://127.0.0.1:8000").strip().rstrip("/")
TIMEOUT = float(os.getenv("KB_TIMEOUT", "10").strip() or "10")
DEFAULT_ASSISTANT = os.getenv("KB_DEFAULT_ASSISTANT", "").strip()
DEFAULT_TOP_K = int(os.getenv("KB_DEFAULT_TOP_K", "5").strip() or "5")
# Below the API's no_answer threshold but at or above this confidence, results are
# usually related rules without the asked fact (eval/ab/report_na1.md); the agent may
# answer partially from them. Below it, results are treated as unrelated.
RELATED_MIN_CONFIDENCE = float(os.getenv("KB_RELATED_MIN_CONFIDENCE", "0.6").strip() or "0.6")

REFERENCE_RULE = (
    "【必须】每次回复末尾都要附「参考文件」清单：按正文首次引用顺序，逐条列出实际引用的文档，"
    "格式为「N. 《文档名》（文号，日期，第 X、Y 页）— 文件名 — 原文链接 — 原始出处」，缺失字段可省略；"
    "日期取 citation.publish_date（仅年份时照实标注），原始出处取 citation.source_url；"
    "同一文档多页合并为一条；正文未引用的检索结果不得列入；"
    "全程未引用任何文档时写「参考文件：无」。正文中的出处标注照常保留。"
)
QUERY_TERMS_RULE = (
    "检索词提示：学生口语与制度用词常不一致——“双学位”通常指文件里的“辅修/辅修学士学位”，"
    "“保研”指“推免”，“一卡通”指“校园卡”。这类问题以正式称谓为主组织查询"
    "（如“辅修学士学位 管理办法 申请条件”），不要只保留口语词。"
)
RETRY_RULE = (
    "【并行多问法检索】同一内容换个说法常能检索到，所以校内问题第一轮就可以在同一次回复里"
    "并行发起多个 kb_search 调用，每个用不同问法，个数由你按问题判断："
    "问题已用制度正式用语、指向明确时一个即可；口语化、可能有多种叫法或指代不清时多发几个，"
    "例如贴近用户原意的完整问句、制度正式用语或文件名式说法"
    "（“××管理办法/规定/实施细则 + 要问的事项”）、同义词/上位概念/负责部门/办理事项名，"
    "或“关键词 关键词”形式；问题含多个要点时，每个要点各发一个调用。"
    "几个问法不能只是调换词序，也不得改变用户原意或引入用户没问的话题。"
    "合并各调用的 results 判断是否找到：要看原文是否真的回答了用户问的那一点，不能只看 answer_mode；"
    "答非所问、只命中开具证明/成绩单等边缘服务、只命中常见问题汇总或只覆盖部分要点，都算没找到。"
    "任一调用找到直接回答就据此作答，不再检索；"
    "都没找到时，再并行发一轮之前没用过的新问法，可去掉 assistant、doc_type 过滤并把 top_k 提到 10；"
    "换了几种说法仍找不到就停止，不要反复检索。"
    "若已定位到相关制度但缺具体条款，可用 kb_documents 确认制度在库，"
    "再用 kb_document_chunks 按 doc_id 读取原文。"
    "不支持并行调用时，按同样的问法依次调用。"
    "以下 answer_mode 的结论，以所有调用中最好的结果为准。"
)
NO_ANSWER_RULE = (
    "按 kb_search 返回的 answer_mode 作答："
    "direct（no_answer=false）——正常基于原文回答；"
    "related（no_answer=true，但有相关内容）——按上面的并行多问法检索后仍为 related 时，"
    "逐条判断 results 是否真的与问题相关：相关则先明确说“知识库中没有直接回答这个问题的内容”，"
    "再给出原文中相关的规定、办理渠道或查询入口并标注出处，"
    "不得把相关内容说成直接答案，不得推断原文没写的具体数字、日期、地点或个人信息；"
    "都不相关则按 none 处理；"
    "none——按上面的并行多问法检索后都为 none，才说明知识库中没有相关信息，"
    "不要基于检索结果作答。"
    "查询个人状态（余额、成绩、审批进度、分配结果等）时知识库无法给出个人数据，"
    "最多给出原文中的查询或办理渠道。"
)
INSTRUCTIONS = (
    "校内制度/FAQ 类问题才调用 kb_search；问候、寒暄、自我介绍、"
    "询问助手能力、闲聊和通用知识问题不要调用任何工具。"
    "短问题可补全为完整问句，但不得改变原意或引入示例话题。"
    "回答必须基于返回原文并标注文档名、条款和页码；"
    "citation.file_url 是可点击的原文链接（带 #page=page_start），回答中一并给出；"
    "citation.source_url 是文档的原始出处链接（官网页面或 AIHub 文档），非空时一并标注；"
    "citation.publish_date 是文档日期（可能仅到年份，date_basis 说明依据），涉及时效的问题须结合该日期判断，"
    "为空表示日期未标定，不得自行推断；"
    "不要编造原文没有的内容。"
    + RETRY_RULE
    + NO_ANSWER_RULE
    + QUERY_TERMS_RULE
    + REFERENCE_RULE
)


def read_env_file_value(path, name):
    for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            return value.strip().strip("\"'")
    return ""


def load_token():
    """KB_API_TOKEN, else KB_API_TOKEN_FILE, else KB_API_TOKEN from KB_API_ENV_FILE."""
    token = os.getenv("KB_API_TOKEN", "").strip()
    token_file = os.getenv("KB_API_TOKEN_FILE", "").strip()
    env_file = os.getenv("KB_API_ENV_FILE", "").strip()
    if not token and token_file:
        try:
            token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            print(f"{SERVER_NAME}: cannot read KB_API_TOKEN_FILE: {exc}", file=sys.stderr)
    if not token and env_file:
        try:
            token = read_env_file_value(env_file, "KB_API_TOKEN")
        except OSError as exc:
            print(f"{SERVER_NAME}: cannot read KB_API_ENV_FILE: {exc}", file=sys.stderr)
    return token


TOKEN = load_token()


class ToolError(Exception):
    pass


def api_request(method, path, payload=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    request = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:300]
        if exc.code == 401:
            raise ToolError("知识库鉴权失败（401），请检查 KB_API_TOKEN、KB_API_TOKEN_FILE 或 KB_API_ENV_FILE。") from exc
        raise ToolError(f"知识库接口返回 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ToolError(
            f"无法连接知识库 {BASE_URL}: {exc.reason}。"
            "本机使用时请先建立隧道：ssh -N -L 8000:127.0.0.1:8000 phoenix"
        ) from exc
    except TimeoutError as exc:
        raise ToolError(f"知识库请求超时（{TIMEOUT:g}s）。") from exc
    except json.JSONDecodeError as exc:
        raise ToolError("知识库接口返回了非 JSON 内容。") from exc


def as_int(value, name, low, high, default):
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{name} 必须是整数。") from exc
    if not low <= number <= high:
        raise ToolError(f"{name} 必须在 {low} 到 {high} 之间。")
    return number


def as_list(value, name):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    raise ToolError(f"{name} 必须是字符串数组。")


def tool_search(arguments):
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ToolError("query 不能为空。")
    payload = {
        "query": query,
        "top_k": as_int(arguments.get("top_k"), "top_k", 1, 20, DEFAULT_TOP_K),
    }
    assistant = str(arguments.get("assistant") or DEFAULT_ASSISTANT).strip()
    if assistant:
        payload["assistant"] = assistant
    doc_types = as_list(arguments.get("doc_type"), "doc_type")
    if doc_types:
        payload["doc_type"] = doc_types

    data = api_request("POST", "/v1/search", payload)
    results = []
    for item in data.get("results") or []:
        citation = item.get("citation") or {}
        results.append(
            {
                "doc_id": item.get("doc_id", ""),
                "chunk_id": item.get("chunk_id", ""),
                "text": item.get("text", ""),
                "score": item.get("score"),
                "rerank_score": item.get("rerank_score"),
                "citation": {
                    "doc_title": citation.get("doc_title", ""),
                    "doc_no": citation.get("doc_no", ""),
                    "doc_type": citation.get("doc_type", ""),
                    "clause_no": citation.get("clause_no", ""),
                    "page_start": citation.get("page_start", 0),
                    "page_end": citation.get("page_end", 0),
                    "section_path": citation.get("section_path") or [],
                    "source_file": citation.get("source_file", ""),
                    "source_url": citation.get("source_url", ""),
                    "publish_date": citation.get("publish_date", ""),
                    "date_basis": citation.get("date_basis", ""),
                    "page_count": citation.get("page_count", 0),
                    "file_url": citation.get("file_url", ""),
                },
            }
        )

    no_answer = bool(data.get("no_answer"))
    confidence = data.get("confidence")
    if not no_answer:
        answer_mode = "direct"
    elif results and isinstance(confidence, (int, float)) and confidence >= RELATED_MIN_CONFIDENCE:
        answer_mode = "related"
    else:
        answer_mode = "none"
    result = {
        "query": data.get("query", query),
        "no_answer": no_answer,
        "answer_mode": answer_mode,
        "confidence": confidence,
        "took_ms": data.get("took_ms"),
        "results": results if answer_mode != "none" else [],
    }
    if answer_mode == "related":
        result["note"] = (
            "answer_mode=related：知识库没有直接回答该问题的条款，以下是可能相关的内容。"
            "先换成更完整、正式的问法重试一次；仍为 related 时，只有确实相关的原文才能使用，"
            "回答须先说明“知识库中没有直接回答这个问题的内容”，再给出相关规定或办理渠道并标注出处，"
            "不得推断原文没写的具体数字、日期、地点或个人信息。"
        )
    elif answer_mode == "none":
        result["note"] = (
            "answer_mode=none：知识库中没有与该问题相关的内容。"
            "请把问题改写为完整、具体的中文问题后重试一次；若仍为 none，"
            "告诉用户知识库没有相关信息，不要基于知识库作答。"
        )
    return result


def tool_meta(arguments):
    return api_request("GET", "/v1/meta")


def tool_documents(arguments):
    data = api_request("GET", "/v1/documents")
    documents = data.get("documents") or []
    assistant = str(arguments.get("assistant") or DEFAULT_ASSISTANT).strip()
    doc_types = as_list(arguments.get("doc_type"), "doc_type")
    if assistant:
        documents = [
            doc
            for doc in documents
            if {assistant, "shared"} & set(doc.get("assistants") or [])
        ]
    if doc_types:
        documents = [doc for doc in documents if doc.get("doc_type") in doc_types]
    keys = (
        "doc_id", "doc_title", "doc_no", "doc_type", "department",
        "assistants", "pages", "chunks", "source_file", "file_url",
        "source_url", "publish_date", "date_basis",
    )
    return {
        "count": len(documents),
        "documents": [{key: doc.get(key) for key in keys} for doc in documents],
    }


def tool_document_chunks(arguments):
    doc_id = str(arguments.get("doc_id") or "").strip()
    if not doc_id:
        raise ToolError("doc_id 不能为空。")
    limit = as_int(arguments.get("limit"), "limit", 1, 50, 20)
    path = "/v1/documents/" + urllib.parse.quote(doc_id, safe="") + "/chunks"
    data = api_request("GET", path)
    chunks = data.get("chunks") or []
    keys = (
        "chunk_id", "text", "clause_no", "page_start", "page_end",
        "section_path", "source_file",
    )
    return {
        "doc_id": data.get("doc_id", doc_id),
        "page_count": data.get("page_count", 0),
        "file_url": data.get("file_url", ""),
        "source_url": data.get("source_url", ""),
        "publish_date": data.get("publish_date", ""),
        "date_basis": data.get("date_basis", ""),
        "total_chunks": len(chunks),
        "returned": min(limit, len(chunks)),
        "chunks": [{key: chunk.get(key) for key in keys} for chunk in chunks[:limit]],
    }


TOOL_DEFINITIONS = [
    {
        "name": "kb_search",
        "description": (
            "检索中国传媒大学校内规章制度与常见问题知识库，返回相关条款、原文和出处。"
            "仅当用户问题属于校内制度、办事流程或校园常见问题（FAQ）时调用；"
            "问候、寒暄、自我介绍、询问你是谁或你能做什么、闲聊和通用知识问题不要调用任何工具，直接回答。"
            "用户问题较短或指代不清时可以补全成完整问句再检索，但只能补全用户已表达的意思，"
            "不得引入示例、知识库内容或无关话题。"
            + RETRY_RULE
            + NO_ANSWER_RULE
            + "问题本不属于校内事务却误调了本工具、结果又无关时，忽略结果并直接回答用户原问题；"
            "属于校内事务的问题，结果无关时按并行多问法检索规则再查，不要直接放弃。"
            "引用时使用 citation 中的 doc_title、doc_no、clause_no、page_start；"
            "citation.file_url 可直接打开原文并跳到 page_start 页，page_count 为文档总页数；"
            "citation.source_url 为原始出处链接（非空时标注），publish_date 为文档日期（可能仅年份）。"
            + QUERY_TERMS_RULE
            + REFERENCE_RULE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 500, "description": "完整的中文检索问题。并行调用时每个调用用不同问法，不要重复用过的查询。"},
                "assistant": {
                    "type": "string",
                    "description": "按助手名过滤（如“中传小勤”）；共享文档始终可见。可先用 kb_meta 查询可选值。",
                },
                "doc_type": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "按文档类型过滤，可传多个。可先用 kb_meta 查询可选值。",
                },
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5, "description": "返回条数。"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "kb_meta",
        "description": (
            "读取知识库概况：文档数、知识块数、可用助手名和文档类型。"
            "只返回全库统计，不含本会话或账号的引用记录；不要用它回答引用次数，"
            "也不要据此声称用户从未引用过任何文档。"
            "仅在需要了解知识库覆盖范围或选择 kb_search 过滤条件时调用；"
            "问候、闲聊或询问助手能力时不要调用。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "kb_documents",
        "description": (
            "列出知识库文档及元数据，可按助手或文档类型过滤。"
            "返回 source_file、页数 pages、原文链接 file_url、原始出处 source_url 和文档日期 publish_date。"
            "用于确认某份制度是否在库中。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "assistant": {"type": "string", "description": "按助手名过滤；共享文档始终可见。"},
                "doc_type": {"type": "array", "items": {"type": "string"}, "description": "按文档类型过滤。"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "kb_document_chunks",
        "description": (
            "按 doc_id 读取文档的原文知识块，用于核对引用或补充上下文。"
            "返回每块的页码和 source_file，以及文档总页数 page_count、原文链接 file_url、"
            "原始出处 source_url 和文档日期 publish_date。"
            "大文档请限制 limit。若据这些内容作答，末尾「参考文件」清单同样要列出该文档的 "
            "doc_title、source_file 和 file_url。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "description": "kb_search 或 kb_documents 返回的 doc_id。"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20, "description": "返回的知识块数量。"},
            },
            "required": ["doc_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
]

TOOL_DESCRIPTIONS = {tool["name"]: tool["description"] for tool in TOOL_DEFINITIONS}

TOOL_HANDLERS = {
    "kb_search": tool_search,
    "kb_meta": tool_meta,
    "kb_documents": tool_documents,
    "kb_document_chunks": tool_document_chunks,
}


def tool_text_result(text, is_error):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def send(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def send_result(request_id, result):
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def send_error(request_id, code, message):
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def handle_call(request_id, params):
    name = params.get("name", "")
    arguments = params.get("arguments") or {}
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        send_error(request_id, -32602, f"未知工具: {name}")
        return
    if not isinstance(arguments, dict):
        send_error(request_id, -32602, "arguments 必须是对象")
        return
    try:
        payload = handler(arguments)
    except ToolError as exc:
        send_result(request_id, tool_text_result(str(exc), True))
        return
    except Exception as exc:
        print(f"{SERVER_NAME}: tool {name} failed: {exc}", file=sys.stderr)
        send_result(request_id, tool_text_result(f"工具执行失败: {exc}", True))
        return
    send_result(request_id, tool_text_result(json.dumps(payload, ensure_ascii=False), False))


def handle(message):
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        requested = str(params.get("protocolVersion") or "")
        version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[-1]
        send_result(
            request_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            },
        )
    elif method == "ping":
        send_result(request_id, {})
    elif method == "tools/list":
        send_result(request_id, {"tools": TOOL_DEFINITIONS})
    elif method == "tools/call":
        handle_call(request_id, params)
    else:
        send_error(request_id, -32601, f"不支持的方法: {method}")


def main():
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            send_error(None, -32700, "JSON 解析失败")
            continue
        if not isinstance(message, dict):
            send_error(None, -32600, "无效请求")
            continue
        if "id" not in message:
            continue
        try:
            handle(message)
        except Exception as exc:
            print(f"{SERVER_NAME}: request failed: {exc}", file=sys.stderr)
            send_error(message.get("id"), -32603, "内部错误")


if __name__ == "__main__":
    main()
