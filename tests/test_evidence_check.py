"""Evidence-level eval harness: coverage, stage diagnosis, verdict parsing."""

import sys
import unittest
from unittest import mock

from tests import support

sys.path.insert(0, str(support.ROOT / "eval"))
import evidence_check  # noqa: E402


def result(doc_id, chunk_id, text):
    return {"doc_id": doc_id, "chunk_id": chunk_id, "text": text, "citation": {"doc_title": doc_id}}


CORPUS = {
    "a-1": ("docA", evidence_check.norm("本专科生每年不超过12000元")),
    "a-2": ("docA", evidence_check.norm("研究生每年不超过16000元")),
    "b-1": ("docB", evidence_check.norm("无关内容")),
}
ITEM = {"doc_id": "docA", "chunk_id": "a-1", "terms": ["本专科生", "12000元"]}


class CoverageTest(unittest.TestCase):
    def test_terms_must_cooccur_in_one_chunk_of_the_right_doc(self):
        self.assertTrue(evidence_check.item_covered(ITEM, [result("docA", "a-1", "本专科生 每年不超过 12000 元")]))
        # split across two chunks: not covered (the agent would need expansion)
        split = [result("docA", "a-x", "本专科生每年"), result("docA", "a-y", "不超过12000元")]
        self.assertFalse(evidence_check.item_covered(ITEM, split))
        self.assertFalse(evidence_check.item_covered(ITEM, [result("docB", "b-1", "本专科生12000元")]))
        self.assertFalse(evidence_check.item_covered(ITEM, []))

    def test_alt_docs_accept_the_same_fact_in_another_document(self):
        hit = [result("docB", "b-9", "本专科生每年不超过12000元")]
        self.assertFalse(evidence_check.item_covered(ITEM, hit))
        alt = {**ITEM, "alt_docs": ["docB"]}
        self.assertTrue(evidence_check.item_covered(alt, hit))
        self.assertEqual(evidence_check.item_first_rank(alt, hit), 1)
        corpus = {**CORPUS, "b-9": ("docB", evidence_check.norm("本专科生每年不超过12000元"))}
        self.assertTrue(evidence_check.stage_evidence_present(alt, ["b-9"], corpus))
        self.assertFalse(evidence_check.stage_evidence_present(ITEM, ["b-9"], corpus))

    def test_first_rank(self):
        results = [result("docB", "b-1", "x"), result("docA", "a-1", "本专科生12000元")]
        self.assertEqual(evidence_check.item_first_rank(ITEM, results), 2)
        self.assertIsNone(evidence_check.item_first_rank(ITEM, results[:1]))


class DiagnoseTest(unittest.TestCase):
    def diagnose(self, dense, bm25, fused, selected, order, final):
        trace = {
            "dense": [{"chunk_id": c, "score": 0.9} for c in dense],
            "bm25": [{"chunk_id": c, "score": 5.0} for c in bm25],
            "fused": [{"chunk_id": c, "score": 0.02} for c in fused],
            "selected": selected,
            "order": order,
        }
        return evidence_check.diagnose_item(ITEM, trace, CORPUS, final)

    def test_ok_when_in_final(self):
        diag = self.diagnose(["a-1"], [], ["a-1", "b-1"], ["a-1", "b-1"], ["a-1", "b-1"], ["a-1"])
        self.assertEqual(diag, {"loss_stage": "ok", "recall_source": "dense"})

    def test_ranking_loss_below_topk_cut(self):
        diag = self.diagnose(["a-1"], ["a-1"], ["a-1", "b-1"], ["b-1", "a-1"], ["b-1", "a-1"], ["b-1"])
        self.assertEqual(diag["loss_stage"], "ranking")
        self.assertEqual(diag["recall_source"], "both")

    def test_per_doc_cap(self):
        filler = [f"a-{i}" for i in range(2, 18)]  # 16 docA chunks already selected
        corpus = {**CORPUS, **{cid: ("docA", "填充") for cid in filler}}
        trace = {
            "dense": [{"chunk_id": "a-1", "score": 0.9}],
            "bm25": [],
            "fused": [{"chunk_id": c, "score": 0.02} for c in ["a-1", *filler]],
            "selected": filler,
            "order": filler,
        }
        diag = evidence_check.diagnose_item(ITEM, trace, corpus, filler[:10])
        self.assertEqual(diag["loss_stage"], "per_doc_cap")

    def test_fusion_pool_cut(self):
        diag = self.diagnose(["a-1"], [], ["a-1", "a-2"], ["a-2"], ["a-2"], ["a-2"])
        self.assertEqual(diag["loss_stage"], "fusion_pool")

    def test_recalled_but_below_fused_pool(self):
        diag = self.diagnose(["a-1"], [], [], [], [], [])
        self.assertEqual(diag, {"loss_stage": "fusion_pool", "recall_source": "dense"})

    def test_not_recalled(self):
        diag = self.diagnose([], [], [], [], [], [])
        self.assertEqual(diag, {"loss_stage": "recall", "recall_source": "none"})

    def test_bm25_only_recall(self):
        diag = self.diagnose([], ["a-1"], ["a-1"], ["a-1"], ["a-1"], ["a-1"])
        self.assertEqual(diag["recall_source"], "bm25")


class VerdictTest(unittest.TestCase):
    def test_plain_and_fenced_json(self):
        for text in (
            '{"correct": true, "complete": false, "fabricated": false, "refused": false, "reason": "ok"}',
            '```json\n{"correct": true, "complete": true, "fabricated": false, "refused": false, "reason": "ok"}\n```',
        ):
            verdict = evidence_check.parse_verdict(text)
            self.assertTrue(verdict["correct"])
            self.assertFalse(verdict["fabricated"])

    def test_garbage_is_a_failed_verdict(self):
        verdict = evidence_check.parse_verdict("I cannot judge this.")
        self.assertFalse(verdict["correct"])
        self.assertIn("not JSON", verdict["reason"])
        self.assertTrue(verdict["_parse_failed"])

    def test_truncated_json_is_a_parse_failure(self):
        verdict = evidence_check.parse_verdict('{"correct": false, "complete": false, "fabricated": false')
        self.assertTrue(verdict["_parse_failed"])


class JudgeRetryTest(unittest.TestCase):
    def test_clean_verdict_does_not_retry(self):
        good = '{"correct": true, "complete": true, "fabricated": false, "refused": false, "reason": "ok"}'
        with mock.patch.object(evidence_check, "llm_chat", return_value=good) as chat:
            verdict = evidence_check.judge_with_retry("q", {})
        self.assertEqual(chat.call_count, 1)
        self.assertTrue(verdict["correct"])
        self.assertNotIn("_parse_failed", verdict)

    def test_retry_recovers_from_truncation(self):
        truncated = '{"correct": false, "complete": false, "fabricated": false'
        good = '{"correct": true, "complete": false, "fabricated": false, "refused": false, "reason": "ok"}'
        with mock.patch.object(evidence_check, "llm_chat", side_effect=[truncated, good]) as chat:
            verdict = evidence_check.judge_with_retry("q", {})
        self.assertEqual(chat.call_count, 2)
        self.assertTrue(verdict["correct"])
        self.assertNotIn("_parse_failed", verdict)

    def test_retry_budget_grows_and_nudge_varies(self):
        truncated = '{"correct": false'
        with mock.patch.object(evidence_check, "llm_chat", return_value=truncated) as chat:
            verdict = evidence_check.judge_with_retry("q", {}, attempts=3)
        self.assertEqual(chat.call_count, 3)
        budgets = [call.kwargs["max_tokens"] for call in chat.call_args_list]
        self.assertEqual(budgets, [512, 1024, 1536])
        users = [call.args[1][-1]["content"] for call in chat.call_args_list]
        self.assertEqual(len(set(users)), 3)  # distinct nudges -> distinct cache keys
        self.assertFalse(verdict["correct"])
        self.assertNotIn("_parse_failed", verdict)


class FragmentLabelTest(unittest.TestCase):
    def test_superseded_fragment_is_flagged(self):
        r = {"citation": {"doc_title": "某办法（2019）", "page_start": 3, "superseded_by": "newdoc"},
             "text": "旧内容"}
        label = evidence_check.fragment_label(r, 1)
        self.assertIn("废止", label)
        self.assertIn("旧内容", label)

    def test_older_edition_fragment_is_flagged(self):
        r = {"text": "额度8000元", "citation": {"doc_title": "说明", "page_start": 1,
                                             "newer_version": "y26", "publish_date": "2021-06-01"}}
        label = evidence_check.fragment_label(r, 1)
        self.assertIn("同名文件已有更新一期", label)
        self.assertIn("2021-06-01", label)

    def test_current_fragment_not_flagged(self):
        r = {"citation": {"doc_title": "某办法（2025）", "page_start": 1, "superseded_by": ""},
             "text": "新内容"}
        label = evidence_check.fragment_label(r, 2)
        self.assertNotIn("废止", label)
        self.assertIn("【片段2】", label)


class SummarizeTest(unittest.TestCase):
    def row(self, case_id, category, answerable, no_answer, hit5, loss=None):
        row = {
            "id": case_id, "category": category, "answerable": answerable,
            "no_answer": no_answer, "degraded": False,
            "evidence_hit@5": hit5, "verified_chunk_hit@5": hit5, "item_coverage@5": 1.0 if hit5 else 0.0,
            "evidence": [],
        }
        for k in (1, 3, 10):
            row[f"evidence_hit@{k}"] = hit5
            row[f"verified_chunk_hit@{k}"] = hit5
            row[f"item_coverage@{k}"] = 1.0 if hit5 else 0.0
        if loss:
            row["evidence"] = [{"loss_stage": loss, "recall_source": "dense"}]
        return row

    def test_counts_and_errors(self):
        rows = [
            self.row("e1", "table", True, False, True, "ok"),
            self.row("e2", "table", True, True, False, "ranking"),  # false refuse
            self.row("e3", "multi_point", True, False, False, "recall"),
            self.row("n1", "no_answer", False, False, False),  # false accept
            self.row("n2", "no_answer", False, True, False),
        ]
        summary = evidence_check.summarize(rows, [100, 200], True)
        self.assertEqual(summary["evidence_hit@5"], 1)
        self.assertEqual(summary["false_refuse"], ["e2"])
        self.assertEqual(summary["false_accept"], ["n1"])
        self.assertEqual(summary["supported_answer@5"], 1)
        self.assertEqual(summary["evidence_loss_stages"], {"ok": 1, "ranking": 1, "recall": 1})
        self.assertEqual(summary["by_category"]["table"], {"total": 2, "evidence_hit@5": 1, "answerable": 2})
        self.assertNotIn("llm", summary)

    def test_llm_block(self):
        row = self.row("e1", "table", True, False, True)
        row["llm"] = {"correct": True, "complete": False, "fabricated": True, "refused": False}
        summary = evidence_check.summarize([row], [100], True)
        self.assertEqual(summary["llm"], {"total": 1, "correct": 1, "complete": 0, "fabricated": 1, "refused": 0})


class ExpansionWatchTest(unittest.TestCase):
    def test_via_expansion_only_when_merged_neighbour(self):
        merged = result("docA", "a-2", "研究生每年不超过16000元 本专科生每年不超过12000元")
        merged["expanded_chunks"] = ["a-1"]
        self.assertTrue(evidence_check.covered_via_expansion(ITEM, [merged]))
        anchor = result("docA", "a-1", "本专科生每年不超过12000元")
        anchor["expanded_chunks"] = ["a-2"]
        self.assertFalse(evidence_check.covered_via_expansion(ITEM, [anchor]))
        self.assertFalse(evidence_check.covered_via_expansion(ITEM, []))
        self.assertFalse(evidence_check.covered_via_expansion({"doc_id": "docA", "terms": ["x"]}, [merged]))

    def test_summarize_split_and_version_blocks(self):
        def entry(chunk_id, rank, via5):
            return {
                "doc_id": "docA", "chunk_id": chunk_id, "first_rank": rank,
                "covered": {str(k): True for k in (1, 3, 5, 10)},
                "via_expansion": {str(k): via5 for k in (1, 3, 5, 10)},
            }

        split = SummarizeTest.row(self, "s1", "split_adjacent", True, False, True)
        split["evidence"] = [entry("a-1", 1, False), entry("a-2", 1, True)]
        version = SummarizeTest.row(self, "v1", "version_superseded", True, False, True)
        version["evidence"] = [entry("a-1", 2, False)]
        version["watch_ranks"] = {"oldDoc": 1}
        summary = evidence_check.summarize([split, version], [100, 200], True)
        self.assertEqual(summary["split"], {"cases": 1, "hit@5": 1, "items_total": 2,
                                            "items_covered_via_expansion@5": 1})
        self.assertEqual(summary["version"], {"cases": 1, "hit@5": 1, "old_above_new": ["v1"]})


if __name__ == "__main__":
    unittest.main()