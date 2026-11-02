"""Streamable HTTP MCP endpoint: token check, Host allow-list and tool listing.

Production runs this from a separate venv (.venv-mcp: mcp, uvicorn, pyjwt), so
CI runs it in its own job; elsewhere it is skipped when `mcp` is missing.
"""

import importlib.util
import unittest

from tests import support

HAS_MCP = importlib.util.find_spec("mcp") is not None
TOKEN = support.TEST_ENV["KB_MCP_TOKEN"]
BASE_URL = "http://127.0.0.1:8765"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(request_id, method, params=None):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


INITIALIZE = rpc(
    1,
    "initialize",
    {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "ci", "version": "0"}},
)


@unittest.skipUnless(HAS_MCP, "mcp package not installed (runs in the mcp-http CI job)")
class McpHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from starlette.testclient import TestClient

        from kb import mcp_http_server

        cls.module = mcp_http_server
        cls.client_cm = TestClient(mcp_http_server.app, base_url=BASE_URL)
        cls.client = cls.client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    def post(self, body, headers=None, url="/mcp"):
        return self.client.post(url, json=body, headers={**MCP_HEADERS, **(headers or {})})

    def test_healthz_is_public(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_token_required(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"X-MCP-Token": "wrong"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(INITIALIZE, headers).status_code, 401)

    def test_token_sources(self):
        for headers, url in (
            ({"Authorization": f"Bearer {TOKEN}"}, "/mcp"),
            ({"X-MCP-Token": TOKEN}, "/mcp"),
            ({}, f"/mcp?token={TOKEN}"),
        ):
            with self.subTest(headers=headers, url=url):
                response = self.post(INITIALIZE, headers, url)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["result"]["serverInfo"]["name"], "cuc-kb")

    def test_unlisted_host_rejected(self):
        response = self.post(INITIALIZE, {"Authorization": f"Bearer {TOKEN}", "Host": "evil.example.test"})
        self.assertIn(response.status_code, (400, 403, 421))

    def test_tools_match_stdio_server(self):
        headers = {"Authorization": f"Bearer {TOKEN}", "MCP-Protocol-Version": "2025-06-18"}
        response = self.post(rpc(2, "tools/list"), headers)
        self.assertEqual(response.status_code, 200, response.text)
        tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
        core = self.module.core
        self.assertEqual(set(tools), set(core.TOOL_HANDLERS))
        for name, description in core.TOOL_DESCRIPTIONS.items():
            with self.subTest(tool=name):
                self.assertEqual(tools[name]["description"], description)
        self.assertEqual(tools["kb_search"]["inputSchema"]["required"], ["query"])
        self.assertEqual(tools["kb_document_chunks"]["inputSchema"]["required"], ["doc_id"])


if __name__ == "__main__":
    unittest.main()
