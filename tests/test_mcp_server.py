"""stdio MCP server: tool logic, protocol handling and agent/ definition sync."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import support

from kb import mcp_server

AGENT_DIR = support.ROOT / "agent"


def search_response(no_answer, confidence, results=1):
    return {
        "query": "q",
        "no_answer": no_answer,
        "confidence": confidence,
        "took_ms": 5,
        "results": [
            {"doc_id": f"d{i}", "chunk_id": f"c{i}", "text": "t", "citation": {"doc_title": "T"}}
            for i in range(results)
        ],
    }


class AgentDefinitionSyncTest(unittest.TestCase):
    """agent/*.json are hand-synced copies of mcp_server.TOOL_DEFINITIONS."""

    def test_openai_tools_match_mcp_definitions(self):
        tools = json.loads((AGENT_DIR / "tools.openai.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [tool["function"]["name"] for tool in tools],
            [tool["name"] for tool in mcp_server.TOOL_DEFINITIONS],
        )
        for tool, definition in zip(tools, mcp_server.TOOL_DEFINITIONS):
            with self.subTest(tool=definition["name"]):
                self.assertEqual(tool["type"], "function")
                self.assertEqual(tool["function"]["description"], definition["description"])
                self.assertEqual(tool["function"]["parameters"], definition["inputSchema"])

    def test_openapi_operations_match_tools(self):
        spec = json.loads((AGENT_DIR / "openapi.json").read_text(encoding="utf-8"))
        operations = {
            operation["operationId"]
            for methods in spec["paths"].values()
            for operation in methods.values()
        }
        self.assertEqual(operations, set(mcp_server.TOOL_HANDLERS))

    def test_openapi_search_schema_matches_tool_bounds(self):
        spec = json.loads((AGENT_DIR / "openapi.json").read_text(encoding="utf-8"))
        api = spec["components"]["schemas"]["SearchRequest"]["properties"]
        tool = mcp_server.TOOL_DEFINITIONS[0]["inputSchema"]["properties"]
        for field in ("query", "top_k"):
            for bound in ("minLength", "maxLength", "minimum", "maximum", "default"):
                with self.subTest(field=field, bound=bound):
                    self.assertEqual(api[field].get(bound), tool[field].get(bound))


class ArgumentValidationTest(unittest.TestCase):
    def test_as_int(self):
        self.assertEqual(mcp_server.as_int(None, "top_k", 1, 20, 5), 5)
        self.assertEqual(mcp_server.as_int("", "top_k", 1, 20, 5), 5)
        self.assertEqual(mcp_server.as_int("7", "top_k", 1, 20, 5), 7)
        for bad in (0, 21, "x", [1]):
            with self.subTest(value=bad), self.assertRaises(mcp_server.ToolError):
                mcp_server.as_int(bad, "top_k", 1, 20, 5)

    def test_as_list(self):
        self.assertIsNone(mcp_server.as_list(None, "doc_type"))
        self.assertEqual(mcp_server.as_list("制度", "doc_type"), ["制度"])
        self.assertEqual(mcp_server.as_list(["制度", " ", 3], "doc_type"), ["制度", "3"])
        with self.assertRaises(mcp_server.ToolError):
            mcp_server.as_list({"a": 1}, "doc_type")

    def test_empty_query_and_doc_id_rejected_before_api_call(self):
        with mock.patch.object(mcp_server, "api_request") as api:
            with self.assertRaises(mcp_server.ToolError):
                mcp_server.tool_search({"query": "  "})
            with self.assertRaises(mcp_server.ToolError):
                mcp_server.tool_document_chunks({"doc_id": ""})
        api.assert_not_called()


class SearchAnswerModeTest(unittest.TestCase):
    def run_search(self, response, **arguments):
        arguments.setdefault("query", "学生公寓床的尺寸")
        with mock.patch.object(mcp_server, "api_request", return_value=response) as api:
            result = mcp_server.tool_search(arguments)
        return result, api

    def test_direct(self):
        result, api = self.run_search(search_response(False, 0.93), top_k=3, doc_type="制度")
        self.assertEqual(result["answer_mode"], "direct")
        self.assertEqual(len(result["results"]), 1)
        self.assertNotIn("note", result)
        method, path, payload = api.call_args.args
        self.assertEqual((method, path), ("POST", "/v1/search"))
        self.assertEqual(payload["top_k"], 3)
        self.assertEqual(payload["doc_type"], ["制度"])

    def test_related_at_threshold(self):
        threshold = mcp_server.RELATED_MIN_CONFIDENCE
        result, _ = self.run_search(search_response(True, threshold))
        self.assertEqual(result["answer_mode"], "related")
        self.assertEqual(len(result["results"]), 1)
        self.assertIn("answer_mode=related", result["note"])

    def test_none_below_threshold_hides_results(self):
        result, _ = self.run_search(search_response(True, mcp_server.RELATED_MIN_CONFIDENCE - 0.01))
        self.assertEqual(result["answer_mode"], "none")
        self.assertEqual(result["results"], [])
        self.assertIn("answer_mode=none", result["note"])

    def test_none_without_results_or_confidence(self):
        for response in (search_response(True, 0.9, results=0), search_response(True, None)):
            with self.subTest(response=response):
                result, _ = self.run_search(response)
                self.assertEqual(result["answer_mode"], "none")

    def test_document_chunks_escapes_doc_id(self):
        response = {"doc_id": "a/../b", "chunks": [{"chunk_id": str(i)} for i in range(30)]}
        with mock.patch.object(mcp_server, "api_request", return_value=response) as api:
            result = mcp_server.tool_document_chunks({"doc_id": "a/../b", "limit": 5})
        self.assertEqual(api.call_args.args[1], "/v1/documents/a%2F..%2Fb/chunks")
        self.assertEqual((result["total_chunks"], result["returned"]), (30, 5))
        self.assertEqual(len(result["chunks"]), 5)

    def test_documents_filter_keeps_shared(self):
        docs = [
            {"doc_id": "1", "assistants": ["中传小勤"], "doc_type": "制度"},
            {"doc_id": "2", "assistants": ["shared"], "doc_type": "制度"},
            {"doc_id": "3", "assistants": ["财务处小助手"], "doc_type": "制度"},
            {"doc_id": "4", "assistants": ["中传小勤"], "doc_type": "FAQ"},
        ]
        with mock.patch.object(mcp_server, "api_request", return_value={"documents": docs}):
            result = mcp_server.tool_documents({"assistant": "中传小勤", "doc_type": ["制度"]})
        self.assertEqual([doc["doc_id"] for doc in result["documents"]], ["1", "2"])


class TokenLoadingTest(unittest.TestCase):
    def test_env_file_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text('# c\nexport KB_API_TOKEN="from-file"\nOTHER=1\n', encoding="utf-8")
            env = {"KB_API_TOKEN": "", "KB_API_ENV_FILE": str(env_file)}
            with mock.patch.dict("os.environ", env):
                self.assertEqual(mcp_server.load_token(), "from-file")


class StdioProtocolTest(unittest.TestCase):
    """Run the real script over stdin/stdout; no tool call reaches the network."""

    def exchange(self, messages):
        lines = "".join(
            (message if isinstance(message, str) else json.dumps(message)) + "\n"
            for message in messages
        )
        completed = subprocess.run(
            [sys.executable, str(support.ROOT / "kb" / "mcp_server.py")],
            input=lines.encode("utf-8"),
            capture_output=True,
            timeout=30,
            check=True,
        )
        return [json.loads(line) for line in completed.stdout.decode("utf-8").splitlines()]

    def test_session(self):
        replies = self.exchange(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "kb_search", "arguments": {"query": ""}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope"}},
                {"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
                "{not json",
            ]
        )
        by_id = {reply.get("id"): reply for reply in replies}
        self.assertEqual(len(replies), 6)  # the notification gets no reply
        self.assertEqual(by_id[1]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(
            [tool["name"] for tool in by_id[2]["result"]["tools"]],
            ["kb_search", "kb_meta", "kb_documents", "kb_document_chunks"],
        )
        self.assertTrue(by_id[3]["result"]["isError"])
        self.assertEqual(by_id[4]["error"]["code"], -32602)
        self.assertEqual(by_id[5]["error"]["code"], -32601)
        self.assertEqual(by_id[None]["error"]["code"], -32700)

    def test_unknown_protocol_falls_back_to_latest(self):
        (reply,) = self.exchange(
            [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}]
        )
        self.assertEqual(reply["result"]["protocolVersion"], mcp_server.SUPPORTED_PROTOCOLS[-1])


if __name__ == "__main__":
    unittest.main()
