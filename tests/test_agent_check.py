"""Agent-level eval harness: tool conversion, the bounded tool loop, summary."""

import json
import sys
import unittest
from unittest import mock

from tests import support

sys.path.insert(0, str(support.ROOT / "eval"))
import agent_check  # noqa: E402


def tool_call(call_id, name, arguments):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


SEARCH_PAYLOAD = {
    "answer_mode": "direct", "confidence": 0.9,
    "results": [{"doc_id": "docA", "chunk_id": "a-1", "text": "本专科生每年不超过12000元",
                 "citation": {"doc_title": "资助办法", "page_start": 3}}],
}


class ToolsTest(unittest.TestCase):
    def test_mcp_definitions_become_openai_functions(self):
        tools = agent_check.openai_tools(agent_check.mcp_server.TOOL_DEFINITIONS)
        names = [t["function"]["name"] for t in tools]
        self.assertIn("kb_search", names)
        search = next(t for t in tools if t["function"]["name"] == "kb_search")
        self.assertEqual(search["type"], "function")
        self.assertEqual(search["function"]["parameters"]["required"], ["query"])

    def test_seen_fragments_from_search_and_chunks(self):
        fragments = agent_check.seen_fragments("kb_search", SEARCH_PAYLOAD)
        self.assertEqual(fragments[0]["doc_id"], "docA")
        chunks = {"doc_id": "docB", "chunks": [{"chunk_id": "b-2", "text": "第三条", "page_start": 2}]}
        self.assertEqual(agent_check.seen_fragments("kb_document_chunks", chunks)[0]["doc_id"], "docB")
        self.assertEqual(agent_check.seen_fragments("kb_meta", {"docs": 1}), [])
        self.assertEqual(agent_check.seen_fragments("kb_search", {"error": "x"}), [])

    def test_unknown_tool_and_tool_error_are_reported_not_raised(self):
        self.assertIn("error", agent_check.call_tool("nope", {}))
        with mock.patch.dict(agent_check.mcp_server.TOOL_HANDLERS,
                             {"kb_search": mock.Mock(side_effect=agent_check.mcp_server.ToolError("坏了"))}):
            self.assertEqual(agent_check.call_tool("kb_search", {"query": "x"}), {"error": "坏了"})


class LoopTest(unittest.TestCase):
    def test_parallel_calls_then_answer(self):
        replies = [
            {"content": "", "tool_calls": [tool_call("1", "kb_search", {"query": "贷款额度"}),
                                           tool_call("2", "kb_search", {"query": "国家助学贷款 额度"})]},
            {"content": "不超过12000元"},
        ]
        with mock.patch.object(agent_check, "chat", side_effect=replies) as chat, \
                mock.patch.object(agent_check, "call_tool", return_value=SEARCH_PAYLOAD):
            answer, trace = agent_check.run_agent("贷款额度", [])
        self.assertEqual(answer, "不超过12000元")
        self.assertEqual((trace["rounds"], len(trace["calls"]), len(trace["fragments"])), (1, 2, 2))
        self.assertEqual(trace["calls"][0]["answer_mode"], "direct")
        # the second request carries both tool results back to the model
        roles = [m["role"] for m in chat.call_args_list[1].args[0]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "tool"])

    def test_round_limit_forces_an_answer_without_tools(self):
        looping = {"content": "", "tool_calls": [tool_call("1", "kb_search", {"query": "x"})]}
        with mock.patch.object(agent_check, "MAX_ROUNDS", 2), \
                mock.patch.object(agent_check, "chat", side_effect=[looping, looping, {"content": "到此为止"}]) as chat, \
                mock.patch.object(agent_check, "call_tool", return_value=SEARCH_PAYLOAD):
            answer, trace = agent_check.run_agent("x", [])
        self.assertEqual((answer, trace["rounds"]), ("到此为止", 2))
        self.assertEqual(chat.call_args_list[-1].args[2], "none")

    def test_judge_context_dedupes_and_caps(self):
        fragment = agent_check.seen_fragments("kb_search", SEARCH_PAYLOAD)[0]
        self.assertEqual(agent_check.judge_context([fragment, fragment]).count("12000"), 1)
        with mock.patch.object(agent_check, "JUDGE_CONTEXT_CHARS", 5):
            self.assertEqual(agent_check.judge_context([fragment]), "")


class JudgeContextTest(unittest.TestCase):
    def test_judge_sees_the_citation_metadata_the_agent_was_told_to_cite(self):
        fragment = {"doc_id": "d", "chunk_id": "d-1", "text": "正文",
                    "citation": {"doc_title": "办法", "page_start": 2, "publish_date": "2024-07-05",
                                 "file_url": "https://kb.example/v1/files/d#page=2"}}
        text = agent_check.judge_fragment(fragment, 1)
        self.assertIn("文档日期：2024-07-05", text)
        self.assertIn("原文链接：https://kb.example/v1/files/d#page=2", text)
        self.assertNotIn("引用信息", agent_check.judge_fragment({"text": "x", "citation": {}}, 1))

    def test_replay_rebuilds_fragments_from_saved_calls_or_queries(self):
        with mock.patch.object(agent_check, "call_tool", return_value=SEARCH_PAYLOAD) as call:
            by_calls = agent_check.replay_fragments(
                {"calls": [{"name": "kb_search", "arguments": {"query": "a", "top_k": 10}}]})
            by_queries = agent_check.replay_fragments({"queries": ["a", "b"]})
        self.assertEqual((len(by_calls), len(by_queries)), (1, 2))
        self.assertEqual(call.call_args_list[0].args, ("kb_search", {"query": "a", "top_k": 10}))


class SummaryTest(unittest.TestCase):
    def test_blocks_and_no_search_list(self):
        rows = [
            {"id": "a", "category": "short_query", "answerable": True, "searches": 3, "rounds": 2,
             "correct": True, "complete": False, "fabricated": False, "refused": False, "evidence_seen": True},
            {"id": "b", "category": "no_answer", "answerable": False, "searches": 0, "rounds": 0,
             "refused": True, "fabricated": False},
        ]
        summary = agent_check.summarize(rows)
        self.assertEqual(summary["answerable"]["correct"], 1)
        self.assertEqual(summary["answerable"]["avg_searches"], 3.0)
        self.assertEqual(summary["unanswerable"]["refused"], 1)
        self.assertEqual(summary["by_category"]["no_answer"], {"total": 1, "refused": 1, "fabricated": 0,
                                                               "avg_searches": 0.0, "avg_rounds": 0.0})
        self.assertEqual(summary["no_search"], ["b"])



class ProbeTest(unittest.TestCase):
    def test_parse_probe_verdict_accepts_known_outcomes_only(self):
        ok = agent_check.parse_probe_verdict('好的 {"outcome": "not_in_kb", "missed": true, "reason": "库里没有"}')
        self.assertEqual(ok, {"outcome": "not_in_kb", "missed": True, "reason": "库里没有"})
        for bad in ('{"outcome": "maybe"}', "不是 JSON", '{"outcome": ', None):
            verdict = agent_check.parse_probe_verdict(bad)
            self.assertEqual(verdict["outcome"], "judge_failed")
            self.assertTrue(verdict["_parse_failed"])

    def test_probe_rows_use_the_probe_judge_and_retry_on_bad_output(self):
        row = {"probe": True, "answerable": True, "answer": "知识库中没有相关内容"}
        replies = ["乱码", '{"outcome": "not_in_kb", "missed": false, "reason": "明确说明没有"}']
        with mock.patch.object(agent_check.ec, "llm_chat", side_effect=replies) as llm:
            judged = agent_check.judge_row({"query": "快递在哪取", "category": "probe"}, row, [])
        self.assertEqual(llm.call_count, 2)
        self.assertEqual(judged["outcome"], "not_in_kb")
        self.assertNotIn("correct", judged)
        self.assertNotIn("_parse_failed", judged)
        self.assertIn("没有检索到任何原文", llm.call_args_list[0].args[1][1]["content"])

    def test_summary_reports_probe_outcomes_apart_from_gold_cases(self):
        base = {"searches": 3, "rounds": 1, "usage": {"prompt": 1, "completion": 1, "reasoning": 0}}
        rows = [
            {**base, "id": "p001", "category": "probe", "probe": True, "answerable": True,
             "persona": "研究生", "outcome": "grounded", "missed": False},
            {**base, "id": "p002", "category": "probe", "probe": True, "answerable": True,
             "persona": "研究生", "outcome": "not_in_kb", "missed": True},
            {**base, "id": "p003", "category": "probe", "probe": True, "answerable": True,
             "persona": "教职工", "outcome": "ungrounded", "missed": False},
            {**base, "id": "g1", "category": "table", "answerable": True, "correct": True},
        ]
        summary = agent_check.summarize(rows)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["answerable"]["total"], 1)
        self.assertNotIn("probe", summary["by_category"])
        self.assertEqual(summary["tokens"]["prompt"], 4)
        probe = summary["probe"]
        self.assertEqual((probe["total"], probe["grounded"], probe["not_in_kb"], probe["ungrounded"]), (3, 1, 1, 1))
        self.assertEqual(probe["missed"], 1)
        self.assertEqual(probe["by_persona"]["研究生"]["total"], 2)
        self.assertEqual(probe["ids"]["ungrounded"], ["p003"])
        self.assertNotIn("probe", agent_check.summarize(rows[3:]))


if __name__ == "__main__":
    unittest.main()
