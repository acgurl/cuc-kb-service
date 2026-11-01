"""Retrieval pipeline on a tiny in-memory corpus.

Embedding model, Qdrant and the cloud reranker are replaced by fakes; BM25,
fusion, bonuses, rerank ordering and the no-answer judgement are the real code.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock

import numpy as np
from rank_bm25 import BM25Okapi

from tests import support  # noqa: F401

import search_core
from search_core import KB, CloudReranker

CHUNKS = [
    {
        "chunk_id": "dorm-1", "doc_id": "dorm", "doc_title": "学生公寓管理办法",
        "doc_no": "中传学字〔2023〕12号", "doc_type": "制度", "assistants": ["中传小勤"],
        "status": "active", "page_start": 3, "page_end": 3,
        "text": "学生公寓床铺尺寸为0.9米×2.0米，由后勤处统一配置。",
    },
    {
        "chunk_id": "fee-1", "doc_id": "fee", "doc_title": "差旅费管理办法",
        "doc_no": "中传财字〔2024〕229号", "doc_type": "制度", "assistants": ["财务处小助手"],
        "status": "active", "page_start": 5, "page_end": 6,
        "text": "教职工出差住宿费按标准报销，超出部分自理。",
    },
    {
        "chunk_id": "guide-1", "doc_id": "guide", "doc_title": "修学指南2025版",
        "doc_no": "", "doc_type": "指南", "assistants": ["shared"],
        "status": "active", "page_start": 40, "page_end": 40,
        "text": "学生公寓床铺、住宿费、报销等常见问题汇总。",
    },
    {
        "chunk_id": "old-1", "doc_id": "old", "doc_title": "学生公寓管理办法（旧）",
        "doc_no": "中传学字〔2015〕3号", "doc_type": "制度", "assistants": ["中传小勤"],
        "status": "superseded", "page_start": 1, "page_end": 1,
        "text": "学生公寓床铺尺寸为0.8米×1.9米。",
    },
]


class FakeModel:
    def query_embed(self, query):
        yield np.ones(4, dtype=np.float32)


class FakeQdrant:
    """Returns preset dense hits, honouring the status/assistant filter like Qdrant."""

    def __init__(self, kb, scores):
        self.kb = kb
        self.scores = scores
        self.filters = []

    def query_points(self, query_filter=None, limit=10, **_):
        self.filters.append(query_filter)
        assistant = doc_types = None
        for condition in query_filter.must:
            if condition.key == "assistants":
                assistant = condition.match.any[0]
            if condition.key == "doc_type":
                doc_types = condition.match.any
        points = [
            SimpleNamespace(payload={"chunk_id": chunk_id}, score=score)
            for chunk_id, score in sorted(self.scores.items(), key=lambda item: -item[1])
            if self.kb._matches(self.kb.chunk_map[chunk_id], assistant, doc_types)
        ]
        return SimpleNamespace(points=points[:limit])


class FakeReranker:
    def __init__(self, by_text):
        self.by_text = by_text
        self.calls = 0

    def rerank(self, query, documents):
        self.calls += 1
        return [self.by_text.get(document, 0.1) for document in documents]


def build_kb(dense, rerank=None):
    kb = KB()
    for chunk in CHUNKS:
        kb._add_chunk({**chunk, "embed_text": chunk["doc_title"] + "\n" + chunk["text"]})
    kb._load_doc_meta()
    kb.bm25 = BM25Okapi([search_core.jieba.lcut_for_search(c["embed_text"]) for c in kb.chunks])
    kb.model = FakeModel()
    kb.client = FakeQdrant(kb, dense)
    if rerank is not None:
        kb.cloud_reranker = FakeReranker(
            {kb.chunk_map[chunk_id]["embed_text"]: score for chunk_id, score in rerank.items()}
        )
    return kb


class PureHelpersTest(unittest.TestCase):
    def test_doc_no_pattern(self):
        for text in ("中传财字〔2024〕229号", "中传党组字[2018] 47号", "（2020）70号"):
            with self.subTest(text=text):
                self.assertIsNotNone(search_core.DOC_NO_RE.search(text))
        self.assertIsNone(search_core.DOC_NO_RE.search("2024年229号楼"))

    def test_rrf_is_decreasing(self):
        self.assertAlmostEqual(search_core.rrf(0), 1 / 61)
        self.assertGreater(search_core.rrf(0), search_core.rrf(1))

    def test_best_split(self):
        items = [("a", 0.9, True), ("b", 0.7, False), ("c", 0.8, False)]
        self.assertEqual(search_core.best_split(items, lambda i: i[2], lambda i: i[1]), (0.9, 0.8))
        self.assertEqual(search_core.best_split([], lambda i: True, lambda i: 0), (None, None))

    def test_generic_detection(self):
        self.assertTrue(search_core.is_generic_chunk({"doc_title": "修学指南2025版（本科）"}))
        self.assertTrue(search_core.is_generic_chunk({"doc_title": "x", "is_generic": True}))
        self.assertFalse(search_core.is_generic_chunk({"doc_title": "学生公寓管理办法"}))
        self.assertFalse(search_core.is_generic_chunk(None))

    def test_attribute_and_answer_span(self):
        self.assertIsNone(search_core.detect_attribute("你好"))
        attribute = search_core.detect_attribute("住宿费多少钱")
        self.assertEqual(attribute, "price")
        entities = search_core.query_entities("住宿费多少钱")
        self.assertTrue(search_core.has_answer_span("本科生住宿费每年1200元。", attribute, entities))
        self.assertFalse(search_core.has_answer_span("住宿费按规定缴纳。", attribute, entities))
        self.assertFalse(search_core.has_answer_span("每年1200元", attribute, set()))

    def test_file_url_and_page_count(self):
        kb = build_kb({})
        self.assertEqual(kb.file_url("fee", 5), "https://kb.example.test/v1/files/fee#page=5")
        self.assertEqual(kb.file_url("a/b"), "https://kb.example.test/v1/files/a%2Fb")
        self.assertEqual(kb.file_url("qa-csv"), "")
        self.assertEqual(kb.page_count("fee"), 6)


class SearchPipelineTest(unittest.TestCase):
    def test_empty_query(self):
        result = build_kb({}).search("   ")
        self.assertEqual((result["results"], result["no_answer"]), ([], True))

    def test_superseded_and_other_assistants_are_filtered(self):
        kb = build_kb({"dorm-1": 0.8, "fee-1": 0.5, "guide-1": 0.6, "old-1": 0.9})
        result = kb.search("学生公寓床铺尺寸", assistant="中传小勤", top_k=10)
        ids = [item["chunk_id"] for item in result["results"]]
        self.assertNotIn("old-1", ids)
        self.assertNotIn("fee-1", ids)
        self.assertIn("guide-1", ids)  # shared docs stay visible
        self.assertEqual(ids[0], "dorm-1")
        citation = result["results"][0]["citation"]
        self.assertEqual((citation["doc_no"], citation["page_start"]), ("中传学字〔2023〕12号", 3))
        self.assertTrue(citation["file_url"].endswith("/v1/files/dorm#page=3"))

    def test_doc_no_in_query_boosts_that_document(self):
        kb = build_kb({"dorm-1": 0.9, "guide-1": 0.8, "fee-1": 0.3})
        result = kb.search("中传财字〔2024〕229号 住宿", top_k=3)
        self.assertEqual(result["results"][0]["doc_id"], "fee")

    def test_without_rerank_dense_floor_decides_no_answer(self):
        with mock.patch.object(search_core, "NO_RERANK_MIN_DENSE", 0.45):
            weak = build_kb({"guide-1": 0.2}).search("天气怎么样")
            strong = build_kb({"dorm-1": 0.7}).search("床铺尺寸")
        self.assertTrue(weak["no_answer"])
        self.assertIsNone(weak["results"][0]["rerank_score"])
        self.assertFalse(strong["no_answer"])
        self.assertEqual(strong["confidence"], 0.7)

    def test_rerank_orders_results_and_sets_confidence(self):
        kb = build_kb({"guide-1": 0.9, "dorm-1": 0.6}, rerank={"dorm-1": 0.92, "guide-1": 0.5})
        with mock.patch.object(search_core, "RERANK_ALWAYS", True):
            result = kb.search("学生公寓床铺尺寸", top_k=2)
        self.assertEqual(kb.cloud_reranker.calls, 1)
        self.assertEqual([item["chunk_id"] for item in result["results"]], ["dorm-1", "guide-1"])
        self.assertEqual(result["confidence"], 0.92)
        self.assertFalse(result["no_answer"])

    def test_rerank_threshold_triggers_no_answer(self):
        kb = build_kb({"dorm-1": 0.9}, rerank={"dorm-1": 0.74})
        with mock.patch.object(search_core, "RERANK_ALWAYS", True), mock.patch.object(
            search_core, "RERANK_MIN_SCORE", 0.75
        ):
            result = kb.search("学生公寓床铺尺寸")
        self.assertTrue(result["no_answer"])
        self.assertEqual(result["confidence"], 0.74)

    def test_generic_doc_penalised_when_specific_is_close(self):
        kb = build_kb({"guide-1": 0.9, "dorm-1": 0.8}, rerank={"guide-1": 0.90, "dorm-1": 0.85})
        with mock.patch.object(search_core, "RERANK_ALWAYS", True):
            result = kb.search("学生公寓床铺尺寸", top_k=2)
        self.assertEqual(result["results"][0]["chunk_id"], "dorm-1")
        self.assertTrue(result["results"][1]["is_generic"])

    def test_failed_rerank_falls_back_to_hybrid_order(self):
        kb = build_kb({"dorm-1": 0.8, "guide-1": 0.5})
        kb.cloud_reranker = mock.Mock(rerank=mock.Mock(side_effect=TimeoutError("slow")))
        with mock.patch.object(search_core, "RERANK_ALWAYS", True):
            result = kb.search("学生公寓床铺尺寸")
        self.assertEqual(result["results"][0]["chunk_id"], "dorm-1")
        self.assertIsNone(result["results"][0]["rerank_score"])

    def test_meta_and_documents(self):
        kb = build_kb({})
        meta = kb.meta()
        self.assertEqual(meta["documents"], 4)
        self.assertNotIn("shared", meta["assistants"])
        self.assertEqual(meta["doc_types"], ["制度", "指南"])
        self.assertEqual({doc["doc_id"] for doc in kb.documents()}, {"dorm", "fee", "guide", "old"})


class RerankHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"body": body, "auth": self.headers.get("Authorization")})
        if "top_n" in body and body["query"] == "reject-top-n":
            status, reply = 400, {"error": "top_n not supported"}
        elif body["query"] == "unordered":
            reply, status = {"data": [{"score": 0.2}, {"score": 0.9}]}, 200
        else:
            count = len(body["documents"])
            results = [{"index": i, "relevance_score": round(0.9 - 0.1 * i, 2)} for i in reversed(range(count))]
            reply, status = {"results": results}, 200
        data = json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class CloudRerankerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), RerankHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        RerankHandler.requests = []
        self.reranker = CloudReranker(self.base, "/compatible-api/v1/reranks", "qwen3-rerank", "k", "authorization", 5)
        self.addCleanup(self.reranker._drop_connection)

    def test_scores_follow_result_index(self):
        self.assertEqual(self.reranker.rerank("q", ["a", "b", "c"]), [0.9, 0.8, 0.7])
        request = RerankHandler.requests[0]
        self.assertEqual(request["auth"], "Bearer k")
        self.assertEqual(request["body"]["top_n"], 3)

    def test_keep_alive_connection_is_reused(self):
        self.reranker.rerank("q", ["a"])
        first = self.reranker._local.conn
        self.reranker.rerank("q", ["a"])
        self.assertIs(self.reranker._local.conn, first)

    def test_retries_without_top_n_on_400(self):
        self.assertEqual(self.reranker.rerank("reject-top-n", ["a", "b"]), [0.9, 0.8])
        self.assertEqual(["top_n" in r["body"] for r in RerankHandler.requests], [True, False])

    def test_results_without_index_use_input_order(self):
        self.assertEqual(self.reranker.rerank("unordered", ["a", "b"]), [0.2, 0.9])


if __name__ == "__main__":
    unittest.main()
