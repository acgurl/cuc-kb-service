"""Ingest table-caption enrichment: captions join embed_text, never text."""

import unittest

from tests import support  # noqa: F401

from kb import ingest


class TableCaptionTest(unittest.TestCase):
    def test_extracts_caption_ending_at_scope_paren(self):
        text = "课程设置和学分要求见表1.1、1.2。表1.1 攻读学术学位博士研究生课程学分要求（文学和艺术学）"
        self.assertEqual(
            ingest.table_caption(text),
            "表1.1 攻读学术学位博士研究生课程学分要求（文学和艺术学）",
        )

    def test_stops_polluted_flattened_tail_at_paren(self):
        text = "表1.2 攻读学术学位博士研究生课程学分要求（工学）学位课程选修课程补修课程公共必修课程"
        self.assertEqual(
            ingest.table_caption(text),
            "表1.2 攻读学术学位博士研究生课程学分要求（工学）",
        )

    def test_bare_cross_reference_skipped(self):
        self.assertEqual(ingest.table_caption("科研训练学分要求见表2"), "")
        self.assertEqual(ingest.table_caption(""), "")
        self.assertEqual(ingest.table_caption("无表格内容"), "")


class ChunkDocumentCaptionTest(unittest.TestCase):
    def record(self, md):
        return {
            "md": md, "title": "某培养工作规定", "doc_id": "docx", "doc_no": "中传研字〔2026〕1号",
            "doc_type": "制度", "department": "研究生院", "assistants": ["shared"],
            "source_file": "x.pdf",
        }

    def test_caption_in_embed_text_not_text(self):
        md = (
            "第十六条课程学习要求。课程设置和学分要求见表1.1。"
            "表1.1 攻读学术学位博士研究生课程学分要求（文学和艺术学）\n"
            "| 项目 | 学分 |\n| --- | --- |\n| 合计 | ≥16 |\n"
        )
        chunks = ingest.chunk_document(self.record(md))
        tables = [c for c in chunks if c["is_table"]]
        self.assertTrue(tables)
        for t in tables:
            self.assertIn("文学和艺术学", t["embed_text"])
            self.assertNotIn("文学和艺术学", t["text"])  # evidence text stays verbatim

    def test_no_caption_table_unchanged(self):
        md = "| 项目 | 学分 |\n| --- | --- |\n| 合计 | ≥16 |\n"
        chunks = ingest.chunk_document(self.record(md))
        tables = [c for c in chunks if c["is_table"]]
        self.assertTrue(tables)
        self.assertNotIn("表1", tables[0]["embed_text"])


if __name__ == "__main__":
    unittest.main()