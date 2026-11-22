#!/usr/bin/env python3
"""Agent-level end-to-end eval: a tool-calling LLM driven by the real MCP contract.

evidence_check.py --llm answers once from the top-5 of a single search. A
production agent instead follows kb/mcp_server.py: its INSTRUCTIONS, the tool
descriptions (parallel multi-phrasing, retry on related/none, reading adjacent
chunks) and the answer_mode notes. This harness gives an OpenAI-compatible
model exactly those instructions and tools, lets it run a bounded tool loop,
then judges the final answer and reports how many searches it spent.

Runs on Phoenix against the loopback API (read-only, no tuning on this set):

  set -a; . ./.env; set +a; export EVIDENCE_LLM_KEY=...
  .venv/bin/python eval/agent_check.py --tag ag31_prod \
      --dataset eval/heldout/dataset_evidence_v3_1.json [--category short_query] [--workers 4]

Results are appended per case to results_agent_<tag>.jsonl, so an interrupted
run resumes where it stopped.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import evidence_check as ec  # noqa: E402
from kb import mcp_server  # noqa: E402

MAX_ROUNDS = int(os.getenv("AGENT_MAX_ROUNDS", "4"))
TOOL_RESULT_CHARS = int(os.getenv("AGENT_TOOL_RESULT_CHARS", "14000"))
JUDGE_CONTEXT_CHARS = int(os.getenv("AGENT_JUDGE_CONTEXT_CHARS", "16000"))
# Answering model may differ from the judge (e.g. DeepSeek answers, qwen-plus judges).
AGENT_LLM_BASE = os.getenv("AGENT_LLM_BASE", "").rstrip("/") or ec.LLM_BASE
AGENT_LLM_KEY = os.getenv("AGENT_LLM_KEY", "") or ec.LLM_KEY
AGENT_LLM_MODEL = os.getenv("AGENT_LLM_MODEL", "") or ec.LLM_MODEL
# DeepSeek V4 style switch: "enabled" / "disabled"; empty = do not send the field.
AGENT_THINKING = os.getenv("AGENT_THINKING", "").strip()
# Bailian (DashScope) style switch: "true" / "false"; empty = do not send the field.
AGENT_ENABLE_THINKING = os.getenv("AGENT_ENABLE_THINKING", "").strip().lower()
AGENT_MAX_TOKENS = int(os.getenv(
    "AGENT_MAX_TOKENS", "8000" if AGENT_THINKING == "enabled" or AGENT_ENABLE_THINKING == "true" else "2000"))

UNANSWERABLE_JUDGE = (
    "你是检索问答评测员。下面的问题在知识库中没有答案（原因见说明）。判断模型回答，只输出 JSON："
    '{"refused": true/false, "fabricated": true/false, "reason": "50字内"}。'
    "refused=回答明确说明知识库没有所问的这项具体信息（可以同时给出相关流程或咨询渠道）；"
    "fabricated=回答对所问的具体信息（数值/电话/地点/时长/个人办理结果等）给出了确定性答案，"
    "且该信息在给定原文中找不到，或把相近但不是所问对象的信息当作答案。"
    "回答逐字引用给定原文中确实存在的内容并注明其适用范围的，不算 fabricated。两者可同时为 false。"
)
# Demand-side probe cases (category "probe") have no gold answer: the judge only
# checks the answer against the fragments the agent actually retrieved.
PROBE_OUTCOMES = ("grounded", "partial", "not_in_kb", "ungrounded")
PROBE_JUDGE = (
    "你是检索问答评测员。下面的问题没有标准答案，只需对照「给定原文」（模型检索到的全部片段）判断模型回答。"
    '只输出 JSON：{"outcome": "grounded/partial/not_in_kb/ungrounded", "missed": true/false, "reason": "60字内"}。'
    "outcome 四选一："
    "grounded=回答了用户所问的事项，且回答中的关键事实（数字、日期、条件、流程、部门、地点等）都能在给定原文中找到依据；"
    "partial=用户所问的一部分有原文依据并已回答，另一部分回答明确说明知识库没有；"
    "not_in_kb=回答明确说明知识库没有用户所问的内容（可以同时给出相关规定或咨询渠道）；"
    "ungrounded=回答对用户所问给出了具体事实，但其中有关键事实在给定原文中找不到依据，"
    "或把相近但不是所问对象的信息当作答案。只要存在这种情况就判 ungrounded。"
    "missed=给定原文其实包含用户所问事项的答案，但模型回答说没有或没有用上。"
)


def openai_tools(definitions):
    """MCP tool definitions -> OpenAI function-calling tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["inputSchema"],
            },
        }
        for tool in definitions
    ]


def chat(messages, tools=None, tool_choice=None, max_tokens=None, usage=None):
    payload = {"model": AGENT_LLM_MODEL, "messages": messages, "temperature": 0,
               "max_tokens": max_tokens or AGENT_MAX_TOKENS}
    if AGENT_THINKING:
        payload["thinking"] = {"type": AGENT_THINKING}
    if AGENT_ENABLE_THINKING:
        payload["enable_thinking"] = AGENT_ENABLE_THINKING == "true"
    if tools:
        payload["tools"] = tools
        payload["parallel_tool_calls"] = True
    if tool_choice:
        payload["tool_choice"] = tool_choice
    request = urllib.request.Request(
        AGENT_LLM_BASE + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + AGENT_LLM_KEY},
    )
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                body = json.loads(response.read().decode("utf-8"))
            if usage is not None:
                info = body.get("usage") or {}
                usage["prompt"] += info.get("prompt_tokens", 0)
                usage["completion"] += info.get("completion_tokens", 0)
                usage["reasoning"] += (info.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
            return body["choices"][0]["message"]
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"LLM call failed: {last}")


def call_tool(name, arguments):
    """Run one MCP tool handler; errors come back as text, like the MCP server does."""
    handler = mcp_server.TOOL_HANDLERS.get(name)
    if handler is None:
        return {"error": f"未知工具: {name}"}
    try:
        return handler(arguments)
    except mcp_server.ToolError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - mirror handle_call: report, do not crash the run
        return {"error": f"工具执行失败: {exc}"}


def seen_fragments(name, payload):
    """Text fragments a tool result exposed to the model, as evidence_check results."""
    if not isinstance(payload, dict):
        return []
    if name == "kb_search":
        return [
            {"doc_id": r.get("doc_id"), "chunk_id": r.get("chunk_id"), "text": r.get("text", ""),
             "citation": r.get("citation") or {}}
            for r in payload.get("results") or []
        ]
    if name == "kb_document_chunks":
        doc_id = payload.get("doc_id")
        return [
            {"doc_id": c.get("doc_id") or doc_id, "chunk_id": c.get("chunk_id"), "text": c.get("text", ""),
             "citation": {"doc_title": payload.get("doc_title", ""), "page_start": c.get("page_start")}}
            for c in payload.get("chunks") or []
        ]
    return []


def run_agent(query, tools):
    """Bounded tool loop. Returns (answer, trace) where trace lists every tool call."""
    messages = [
        {"role": "system", "content": mcp_server.INSTRUCTIONS},
        {"role": "user", "content": query},
    ]
    calls, fragments, rounds = [], [], 0
    usage = {"prompt": 0, "completion": 0, "reasoning": 0}
    while True:
        force_answer = rounds >= MAX_ROUNDS
        message = chat(messages, tools, "none" if force_answer else None, usage=usage)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls or force_answer:
            return message.get("content") or "", {"calls": calls, "fragments": fragments, "rounds": rounds,
                                                  "usage": usage}
        rounds += 1
        assistant = {"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls}
        if message.get("reasoning_content"):
            # thinking-mode tool loops must hand the reasoning back within the same turn
            assistant["reasoning_content"] = message["reasoning_content"]
        messages.append(assistant)
        for tool_call in tool_calls:
            name = tool_call["function"]["name"]
            try:
                arguments = json.loads(tool_call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            payload = call_tool(name, arguments if isinstance(arguments, dict) else {})
            entry = {"round": rounds, "name": name, "arguments": arguments}
            if name == "kb_search" and isinstance(payload, dict):
                entry["answer_mode"] = payload.get("answer_mode")
                entry["confidence"] = payload.get("confidence")
                entry["degraded"] = bool(payload.get("degraded"))
            calls.append(entry)
            fragments.extend(seen_fragments(name, payload))
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": json.dumps(payload, ensure_ascii=False)[:TOOL_RESULT_CHARS],
            })


CITATION_FIELDS = (
    ("doc_no", "文号"), ("publish_date", "文档日期"), ("source_file", "文件名"),
    ("file_url", "原文链接"), ("source_url", "原始出处"),
)


def judge_fragment(fragment, index):
    """Fragment text plus the citation metadata the agent was shown.

    The agent is told to cite dates, file names and links from the citation, so
    the judge must see them too; otherwise every cited date or URL looks made up.
    """
    citation = fragment.get("citation") or {}
    meta = "；".join(f"{label}：{citation[key]}" for key, label in CITATION_FIELDS if citation.get(key))
    label = ec.fragment_label(fragment, index)
    return f"{label}\n〔引用信息 {meta}〕" if meta else label


def judge_context(fragments):
    """De-duplicated fragment texts for the judge, capped in size."""
    parts, seen, size = [], set(), 0
    for index, fragment in enumerate(fragments, 1):
        key = fragment.get("chunk_id") or fragment.get("text", "")[:80]
        if key in seen:
            continue
        seen.add(key)
        text = judge_fragment(fragment, index)
        if size + len(text) > JUDGE_CONTEXT_CHARS:
            break
        parts.append(text)
        size += len(text)
    return "\n\n".join(parts)


def judge_unanswerable(case, context, answer, cache):
    verdict = None
    for attempt in range(3):
        user = (
            f"用户问题：{case['query']}\n说明：{case.get('reason') or case.get('note') or ''}\n"
            f"无答案探测词：{case.get('probes')}\n给定原文：\n{context}\n\n模型回答：\n{answer}" + " " * attempt
        )
        text = ec.llm_chat(
            ec.JUDGE_MODEL,
            [{"role": "system", "content": UNANSWERABLE_JUDGE}, {"role": "user", "content": user}],
            cache, max_tokens=300 + 300 * attempt,
        )
        verdict = ec.parse_verdict(text)
        if not verdict.get("_parse_failed"):
            break
    return {"refused": verdict["refused"], "fabricated": verdict["fabricated"], "reason": verdict["reason"]}


def parse_probe_verdict(text):
    """Probe judge output -> {"outcome", "missed", "reason"}; unknown outcomes fail the parse."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        verdict = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        verdict = None
    if not isinstance(verdict, dict) or verdict.get("outcome") not in PROBE_OUTCOMES:
        return {"outcome": "judge_failed", "missed": False, "_parse_failed": True,
                "reason": f"judge output unusable: {(text or '')[:120]}"}
    return {"outcome": verdict["outcome"], "missed": bool(verdict.get("missed")),
            "reason": str(verdict.get("reason", ""))[:300]}


def judge_probe(case, context, answer, cache):
    verdict = None
    for attempt in range(3):
        user = (
            f"用户问题：{case['query']}\n给定原文：\n{context or '（模型没有检索到任何原文）'}\n\n"
            f"模型回答：\n{answer}" + " " * attempt
        )
        text = ec.llm_chat(
            ec.JUDGE_MODEL,
            [{"role": "system", "content": PROBE_JUDGE}, {"role": "user", "content": user}],
            cache, max_tokens=300 + 300 * attempt,
        )
        verdict = parse_probe_verdict(text)
        if not verdict.get("_parse_failed"):
            break
    verdict.pop("_parse_failed", None)
    return verdict


def replay_fragments(row):
    """Re-run a saved row's tool calls to rebuild what the agent saw (rejudge)."""
    calls = row.get("calls") or [{"name": "kb_search", "arguments": {"query": q}} for q in row.get("queries") or []]
    fragments = []
    for call in calls:
        fragments.extend(seen_fragments(call["name"], call_tool(call["name"], call.get("arguments") or {})))
    return fragments


def judge_row(case, row, fragments):
    """Attach verdict fields to a row from its answer and the fragments it saw."""
    context = judge_context(fragments)
    cache = {}
    if row.get("probe"):
        row.update(judge_probe(case, context, row["answer"], cache))
    elif row["answerable"]:
        evidence = case.get("evidence", [])
        row["evidence_seen"] = bool(evidence) and all(ec.item_covered(item, fragments) for item in evidence)
        judge_user = (
            f"用户问题：{case['query']}\n标准答案：{case.get('answer', '')}\n"
            f"必答要点：{json.dumps(case.get('required_points') or [], ensure_ascii=False)}\n"
            f"给定原文：\n{context}\n\n模型回答：\n{row['answer']}"
        )
        row.update(ec.judge_with_retry(judge_user, cache))
    else:
        row.update(judge_unanswerable(case, context, row["answer"], cache))
    return row


def evaluate_case(case, tools):
    started = time.time()
    answer, trace = run_agent(case["query"], tools)
    fragments = trace["fragments"]
    searches = [c for c in trace["calls"] if c["name"] == "kb_search"]
    row = {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "answerable": case.get("answerable", True),
        "probe": case["category"] == "probe",
        "persona": case.get("persona"),
        "stage": case.get("stage"),
        "rounds": trace["rounds"],
        "searches": len(searches),
        "other_calls": len(trace["calls"]) - len(searches),
        "queries": [c["arguments"].get("query") for c in searches],
        "answer_modes": [c.get("answer_mode") for c in searches],
        "degraded_searches": sum(bool(c.get("degraded")) for c in searches),
        "usage": trace.get("usage"),
        "calls": [{"name": c["name"], "arguments": c["arguments"]} for c in trace["calls"]],
        "answer": answer,
        "took_s": round(time.time() - started, 1),
    }
    return judge_row(case, row, fragments)


def summarize(rows):
    def block(items, keys):
        out = {"total": len(items)}
        for key in keys:
            out[key] = sum(bool(r.get(key)) for r in items)
        if items:
            out["avg_searches"] = round(sum(r["searches"] for r in items) / len(items), 2)
            out["avg_rounds"] = round(sum(r["rounds"] for r in items) / len(items), 2)
        return out

    probes = [r for r in rows if r.get("probe")]
    total = len(rows)
    rows = [r for r in rows if not r.get("probe")]
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    by_category = {}
    for row in rows:
        by_category.setdefault(row["category"], []).append(row)
    probe = None
    if probes:
        def outcomes(items):
            names = (*PROBE_OUTCOMES, "judge_failed")
            counts = {name: sum(r.get("outcome") == name for r in items) for name in names}
            return {"total": len(items), **{k: v for k, v in counts.items() if v or k in PROBE_OUTCOMES}}

        by_persona = {}
        for row in probes:
            by_persona.setdefault(row.get("persona") or "", []).append(row)
        probe = {
            **outcomes(probes),
            "missed": sum(bool(r.get("missed")) for r in probes),
            "avg_searches": round(sum(r["searches"] for r in probes) / len(probes), 2),
            "avg_rounds": round(sum(r["rounds"] for r in probes) / len(probes), 2),
            "by_persona": {name: outcomes(items) for name, items in by_persona.items()},
            "ids": {name: [r["id"] for r in probes if r.get("outcome") == name]
                    for name in ("partial", "not_in_kb", "ungrounded", "judge_failed")},
        }
    every = rows + probes
    answer_keys = ("correct", "complete", "fabricated", "refused", "evidence_seen")
    refuse_keys = ("refused", "fabricated")
    return {
        "total": total,
        **({"probe": probe} if probe else {}),
        "answerable": block(answerable, answer_keys),
        "unanswerable": block(unanswerable, refuse_keys),
        "by_category": {
            name: block(items, answer_keys if items[0]["answerable"] else refuse_keys)
            for name, items in by_category.items()
        },
        "no_search": [r["id"] for r in every if r["searches"] == 0],
        "degraded_searches": sum(r.get("degraded_searches", 0) for r in every),
        "tokens": {k: sum((r.get("usage") or {}).get(k, 0) for r in every)
                   for k in ("prompt", "completion", "reasoning")},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dataset", default=str(ROOT / "eval/heldout/dataset_evidence_v3_1.json"))
    parser.add_argument("--category", help="逗号分隔的类别")
    parser.add_argument("--ids", help="逗号分隔的 case id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--rejudge-from", metavar="TAG",
        help="不重新作答：读取 results_agent_<TAG>.jsonl 的回答，重放其工具调用还原所见原文后重新判定",
    )
    args = parser.parse_args()
    if not ec.LLM_KEY:
        sys.exit("set EVIDENCE_LLM_KEY (or DASHSCOPE_API_KEY)")
    if not mcp_server.TOKEN:
        sys.exit("set KB_API_TOKEN")

    cases = json.loads(Path(args.dataset).read_text(encoding="utf-8"))["cases"]
    if args.category:
        wanted = set(args.category.split(","))
        cases = [c for c in cases if c["category"] in wanted]
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]

    rows_path = ROOT / f"eval/heldout/results_agent_{args.tag}.jsonl"
    rows = []
    if rows_path.exists():
        rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    done = {r["id"] for r in rows}
    todo = [c for c in cases if c["id"] not in done]
    tools = openai_tools(mcp_server.TOOL_DEFINITIONS)
    lock = threading.Lock()
    saved = {}
    if args.rejudge_from:
        source = ROOT / f"eval/heldout/results_agent_{args.rejudge_from}.jsonl"
        saved = {r["id"]: r for r in map(json.loads, source.read_text(encoding="utf-8").splitlines())}
        todo = [c for c in todo if c["id"] in saved]

    def work(case):
        try:
            if args.rejudge_from:
                row = judge_row(case, dict(saved[case["id"]]), replay_fragments(saved[case["id"]]))
            else:
                row = evaluate_case(case, tools)
        except Exception as exc:  # noqa: BLE001 - one failed case must not stop the run
            print(f"{case['id']} FAILED: {exc}", flush=True)
            return
        with lock:
            rows.append(row)
            with rows_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            flags = " ".join(f"{k}={int(bool(row.get(k)))}" for k in ("correct", "complete", "fabricated", "refused")
                             if k in row)
            if row.get("probe"):
                flags = f"outcome={row.get('outcome')} missed={int(bool(row.get('missed')))}"
            print(f"{row['id']} [{row['category']}] searches={row['searches']} rounds={row['rounds']} {flags}",
                  flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, todo))

    wanted_ids = {c["id"] for c in cases}
    summary = {"tag": args.tag, "dataset": Path(args.dataset).name, "model": AGENT_LLM_MODEL,
               "thinking": AGENT_THINKING or None, "judge_model": ec.JUDGE_MODEL,
               **summarize([r for r in rows if r["id"] in wanted_ids])}
    out = ROOT / f"eval/heldout/results_agent_{args.tag}_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
