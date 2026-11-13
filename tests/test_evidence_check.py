"""Evidence-level eval harness: coverage, stage diagnosis, verdict parsing."""

import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()