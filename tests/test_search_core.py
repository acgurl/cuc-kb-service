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

from kb import search_core
from kb.search_core import KB, CloudReranker

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


def build_kb(dense, rerank=None, chunks=None):
    kb = KB()
    for chunk in chunks if chunks is not None else CHUNKS:
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
        self.assertTrue(
            search_core.is_generic_chunk(
                {"doc_title": "2022年中国传媒大学学生工作规章制度汇编"}
            )
        )
        self.assertTrue(
            search_core.is_generic_chunk(
                {"doc_title": "中国传媒大学学生工作规章制度汇编2026-转换之后"}
            )
        )
        self.assertFalse(search_core.is_generic_chunk({"doc_title": "学生公寓管理办法"}))
        self.assertFalse(search_core.is_generic_chunk(None))

    def test_version_family_and_edition_year(self):
        family = "中国传媒大学学生工作规章制度汇编"
        self.assertEqual(
            search_core.version_family("2022年中国传媒大学学生工作规章制度汇编"), family
        )
        self.assertEqual(
            search_core.version_family("中国传媒大学学生工作规章制度汇编2026-转换之后"), family
        )
        self.assertIsNone(search_core.version_family("学生公寓管理办法"))
        self.assertEqual(search_core.edition_year("2022年中国传媒大学学生工作规章制度汇编"), 2022)
        self.assertEqual(
            search_core.edition_year("中国传媒大学学生工作规章制度汇编2026-转换之后"), 2026
        )

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

    def test_newer_compilation_edition_outranks_older(self):
        chunks = [
            {
                "chunk_id": "comp22-1", "doc_id": "comp22",
                "doc_title": "2022年中国传媒大学学生工作规章制度汇编",
                "doc_type": "制度", "assistants": ["中传教务君"], "status": "active",
                "page_start": 284, "page_end": 284,
                "text": "国家助学贷款：本专科生每年不超过12000元。",
            },
            {
                "chunk_id": "comp26-1", "doc_id": "comp26",
                "doc_title": "中国传媒大学学生工作规章制度汇编2026-转换之后",
                "doc_type": "制度", "assistants": ["中传教务君"], "status": "active",
                "page_start": 337, "page_end": 337,
                "text": "国家助学贷款：本专科生每年不超过12000元。",
            },
        ]
        kb = build_kb(
            {"comp22-1": 0.9, "comp26-1": 0.88},
            rerank={"comp22-1": 0.916, "comp26-1": 0.904},
            chunks=chunks,
        )
        with mock.patch.object(search_core, "RERANK_ALWAYS", True):
            result = kb.search("国家助学贷款管理办法", top_k=2)
        self.assertEqual(
            [item["chunk_id"] for item in result["results"]], ["comp26-1", "comp22-1"]
        )

    def test_failed_rerank_falls_back_to_hybrid_order(self):
        kb = build_kb({"dorm-1": 0.8, "guide-1": 0.5})
        kb.cloud_reranker = mock.Mock(rerank=mock.Mock(side_effect=TimeoutError("slow")))
        with mock.patch.object(search_core, "RERANK_ALWAYS", True):
            result = kb.search("学生公寓床铺尺寸")
        self.assertEqual(result["results"][0]["chunk_id"], "dorm-1")
        self.assertIsNone(result["results"][0]["rerank_score"])
        # The hybrid floor would accept this (dense 0.8); the default policy refuses.
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degraded_reason"], "rerank_unavailable")
        self.assertTrue(result["no_answer"])

    def test_failed_rerank_keep_policy_uses_hybrid_floor(self):
        kb = build_kb({"dorm-1": 0.8, "guide-1": 0.5})
        kb.cloud_reranker = mock.Mock(rerank=mock.Mock(side_effect=TimeoutError("slow")))
        with mock.patch.object(search_core, "RERANK_ALWAYS", True), mock.patch.object(
            search_core, "RERANK_FAILURE_POLICY", "keep"
        ):
            result = kb.search("学生公寓床铺尺寸")
        self.assertTrue(result["degraded"])
        self.assertFalse(result["no_answer"])

    def test_successful_or_skipped_rerank_is_not_degraded(self):
        kb = build_kb({"dorm-1": 0.9}, rerank={"dorm-1": 0.92})
        with mock.patch.object(search_core, "RERANK_ALWAYS", True):
            reranked = kb.search("学生公寓床铺尺寸")
        skipped = build_kb({"dorm-1": 0.9}).search("学生公寓床铺尺寸")
        for result in (reranked, skipped):
            self.assertFalse(result["degraded"])
            self.assertIsNone(result["degraded_reason"])

    def test_meta_and_documents(self):
        kb = build_kb({})
        meta = kb.meta()
        self.assertEqual(meta["documents"], 4)
        self.assertNotIn("shared", meta["assistants"])
        self.assertEqual(meta["doc_types"], ["制度", "指南"])
        self.assertEqual({doc["doc_id"] for doc in kb.documents()}, {"dorm", "fee", "guide", "old"})


class TraceTest(unittest.TestCase):
    """Opt-in stage snapshot used by eval/evidence_check.py for loss diagnosis."""

    def test_trace_off_by_default(self):
        result = build_kb({"dorm-1": 0.8}).search("床铺尺寸")
        self.assertNotIn("trace", result)

    def test_trace_records_every_stage(self):
        kb = build_kb({"guide-1": 0.9, "dorm-1": 0.6}, rerank={"dorm-1": 0.92, "guide-1": 0.5})
        with mock.patch.object(search_core, "RERANK_ALWAYS", True):
            result = kb.search("学生公寓床铺尺寸", top_k=1, trace=True)
        trace = result["trace"]
        self.assertEqual([h["chunk_id"] for h in trace["dense"]], ["guide-1", "dorm-1"])
        self.assertIn("dorm-1", [h["chunk_id"] for h in trace["bm25"]])
        self.assertEqual({h["chunk_id"] for h in trace["fused"]}, {"dorm-1", "guide-1"})
        self.assertEqual(set(trace["selected"]), {"dorm-1", "guide-1"})
        self.assertTrue(trace["rerank_triggered"])
        self.assertFalse(trace["rerank_failed"])
        self.assertEqual(trace["rerank_scores"]["dorm-1"], 0.92)
        self.assertEqual(trace["order"][0], "dorm-1")
        self.assertEqual(trace["final"], ["dorm-1"])

    def test_trace_marks_rerank_failure(self):
        kb = build_kb({"dorm-1": 0.8})
        kb.cloud_reranker = mock.Mock(rerank=mock.Mock(side_effect=TimeoutError("slow")))
        with mock.patch.object(search_core, "RERANK_ALWAYS", True):
            result = kb.search("学生公寓床铺尺寸", trace=True)
        self.assertTrue(result["trace"]["rerank_triggered"])
        self.assertTrue(result["trace"]["rerank_failed"])
        self.assertEqual(result["trace"]["rerank_scores"], {})

    def test_trace_without_reranker(self):
        result = build_kb({"dorm-1": 0.8}).search("床铺尺寸", top_k=1, trace=True)
        self.assertFalse(result["trace"]["rerank_triggered"])
        self.assertEqual(result["trace"]["final"], ["dorm-1"])


class ExpansionTest(unittest.TestCase):
    """Opt-in adjacent-chunk expansion: read aid, ranking unchanged."""

    def setUp(self):
        self.chunks = [
            {
                "chunk_id": f"reg-000{i}", "doc_id": "reg", "doc_title": "某办法",
                "doc_type": "制度", "assistants": ["shared"], "status": "active",
                "page_start": i + 1, "page_end": i + 1, "text": text,
            }
            for i, text in enumerate(["第一条 总则内容。", "第二条 核心条款。", "第三条 例外规定。"])
        ]

    def test_off_by_default(self):
        kb = build_kb({"reg-0001": 0.9}, chunks=self.chunks)
        item = kb.search("核心条款", top_k=1)["results"][0]
        self.assertNotIn("expanded", item)
        self.assertEqual(item["text"], "第二条 核心条款。")

    def test_merges_active_neighbors(self):
        kb = build_kb({"reg-0001": 0.9}, chunks=self.chunks)
        with mock.patch.object(search_core, "EXPAND_ADJACENT", 1):
            item = kb.search("核心条款", top_k=1)["results"][0]
        self.assertTrue(item["expanded"])
        self.assertEqual(item["expanded_chunks"], ["reg-0000", "reg-0002"])
        self.assertIn("第一条", item["text"])
        self.assertIn("第三条", item["text"])
        self.assertEqual((item["citation"]["page_start"], item["citation"]["page_end"]), (1, 3))
        self.assertEqual(item["chunk_id"], "reg-0001")  # anchor id unchanged

    def test_skips_superseded_neighbor(self):
        chunks = [dict(c) for c in self.chunks]
        chunks[2]["status"] = "superseded"
        kb = build_kb({"reg-0001": 0.9}, chunks=chunks)
        with mock.patch.object(search_core, "EXPAND_ADJACENT", 1):
            item = kb.search("核心条款", top_k=1)["results"][0]
        self.assertEqual(item["expanded_chunks"], ["reg-0000"])
        self.assertNotIn("第三条", item["text"])

    def test_edge_anchor_has_one_side(self):
        kb = build_kb({"reg-0000": 0.9}, chunks=self.chunks)
        with mock.patch.object(search_core, "EXPAND_ADJACENT", 2):
            item = kb.search("总则", top_k=1)["results"][0]
        self.assertEqual(item["expanded_chunks"], ["reg-0001", "reg-0002"])

    def test_non_numeric_suffix_not_expanded(self):
        chunks = [{
            "chunk_id": "qa-uuid", "doc_id": "qa", "doc_title": "问答", "doc_type": "问答",
            "assistants": ["shared"], "status": "active", "page_start": 1, "page_end": 1,
            "text": "问答内容。",
        }]
        kb = build_kb({"qa-uuid": 0.9}, chunks=chunks)
        with mock.patch.object(search_core, "EXPAND_ADJACENT", 2):
            item = kb.search("问答", top_k=1)["results"][0]
        self.assertNotIn("expanded", item)


class SupersededTest(unittest.TestCase):
    """Relation-driven version preference: old yields only when new is present."""

    def setUp(self):
        self.chunks = [
            {
                "chunk_id": "old-0000", "doc_id": "old", "doc_title": "某办法（2024）",
                "doc_type": "制度", "assistants": ["shared"], "status": "active",
                "page_start": 1, "page_end": 1, "text": "转专业春季学期组织。",
                "superseded_by": "new",
            },
            {
                "chunk_id": "new-0000", "doc_id": "new", "doc_title": "某办法（2025修订）",
                "doc_type": "制度", "assistants": ["shared"], "status": "active",
                "page_start": 1, "page_end": 1, "text": "转专业秋季学期组织。",
            },
        ]

    def test_old_yields_when_new_present(self):
        kb = build_kb({"old-0000": 0.9, "new-0000": 0.88},
                      rerank={"old-0000": 0.90, "new-0000": 0.89}, chunks=self.chunks)
        with mock.patch.object(search_core, "RERANK_ALWAYS", True):
            result = kb.search("转专业哪个学期", top_k=2)
        self.assertEqual([r["chunk_id"] for r in result["results"]], ["new-0000", "old-0000"])

    def test_old_kept_when_new_absent(self):
        kb = build_kb({"old-0000": 0.9}, rerank={"old-0000": 0.90}, chunks=self.chunks)
        with mock.patch.object(search_core, "RERANK_ALWAYS", True):
            result = kb.search("转专业哪个学期", top_k=2)
        self.assertEqual([r["chunk_id"] for r in result["results"]], ["old-0000"])

    def test_no_rerank_path_demotes(self):
        kb = build_kb({"old-0000": 0.9, "new-0000": 0.88}, chunks=self.chunks)
        result = kb.search("转专业哪个学期", top_k=2)
        self.assertEqual(result["results"][0]["chunk_id"], "new-0000")


class SparseBM25Test(unittest.TestCase):
    """The postings-based scorer must be bit-identical to rank_bm25."""

    QUERIES = [
        "学生公寓床铺尺寸",
        "住宿 住宿 报销",  # duplicate terms accumulate twice, like get_scores
        "完全不存在的词xyz",
        "差旅费管理办法教职工出差",
        "学生",  # single common term
    ]

    def test_scores_bit_identical_to_rank_bm25(self):
        kb = build_kb({})
        kb._build_bm25_index()
        for query in self.QUERIES:
            with self.subTest(query=query):
                tokens = search_core.jieba.lcut_for_search(query)
                expected = kb.bm25.get_scores(tokens)
                actual = kb._bm25_scores(tokens)
                self.assertTrue(np.array_equal(actual, expected))

    def test_search_matches_legacy_filtering(self):
        kb = build_kb({})
        kb._build_bm25_index()

        def legacy(query, limit, assistant, doc_types):
            scores = kb.bm25.get_scores(search_core.jieba.lcut_for_search(query))
            candidates = []
            for index, score in enumerate(scores):
                if score <= 0:
                    continue
                chunk = kb.chunks[index]
                if kb._matches(chunk, assistant, doc_types):
                    candidates.append((chunk["chunk_id"], float(score)))
            candidates.sort(key=lambda item: item[1], reverse=True)
            return candidates[:limit]

        filters = [
            (None, None),
            ("中传小勤", None),
            ("财务处小助手", None),
            ("不存在的助手", None),
            (None, ["制度"]),
            (None, ["制度", "指南"]),
            (None, ["不存在的类型"]),
            ("中传小勤", ["指南"]),
        ]
        for query in self.QUERIES:
            for assistant, doc_types in filters:
                with self.subTest(query=query, assistant=assistant, doc_types=doc_types):
                    self.assertEqual(
                        kb.bm25_search(query, 40, assistant, doc_types),
                        legacy(query, 40, assistant, doc_types),
                    )

    def test_lazy_index_build_without_load(self):
        kb = build_kb({})
        self.assertIsNone(kb.bm25_postings)
        hits = kb.bm25_search("学生公寓床铺", 10, None, None)
        self.assertIsNotNone(kb.bm25_postings)
        self.assertTrue(hits)


class EmbedCacheTest(unittest.TestCase):
    def test_repeated_query_reuses_vector(self):
        kb = build_kb({})
        original = kb.model.query_embed
        with mock.patch.object(
            kb.model, "query_embed", side_effect=original
        ) as query_embed:
            first = kb.encode_query("床铺尺寸")
            second = kb.encode_query("床铺尺寸")
        self.assertEqual(query_embed.call_count, 1)
        self.assertIs(first, second)

    def test_cache_evicts_oldest(self):
        kb = build_kb({})
        with mock.patch.object(search_core, "EMBED_QUERY_CACHE_SIZE", 1):
            kb.encode_query("甲")
            kb.encode_query("乙")
            self.assertEqual(list(kb._embed_cache), ["乙"])


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
