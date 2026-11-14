#!/usr/bin/env python3
"""Evidence-level + stage-diagnostic eval on the blind evidence set.

Unlike live_check_v2.py (document-title / doc_id hits), this harness checks
whether the returned *chunks* actually contain the verified answer evidence,
and — via the opt-in search trace — at which stage the evidence was lost:
dense/BM25 recall -> RRF fusion pool -> per-doc candidate cap -> rerank order
-> top_k cut. The v2 sets stay untouched as the regression baseline.

Runs on Phoenix against the loopback API (no tuning on this set):

  KB_API_TOKEN=$(grep '^KB_API_TOKEN=' .env | cut -d= -f2-) \
  .venv/bin/python eval/evidence_check.py --tag ev1_baseline [--category table] [--llm]

Ground truth is validated against KB_DATA_DIR (default: data/, which equals
data-v9 on Phoenix). --llm adds an optional answer-generation + judging pass
(EVIDENCE_LLM_BASE / EVIDENCE_LLM_KEY / EVIDENCE_LLM_MODEL, OpenAI-compatible);
LLM verdicts are reported separately from the deterministic evidence metrics.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
from kbeval import norm, percentile, request_json  # noqa: E402

API = os.getenv("KB_API_URL", "http://127.0.0.1:8000")
TOKEN = os.getenv("KB_API_TOKEN", "")
TOP_K = 10
CUTS = (1, 3, 5, 10)
MAX_CHUNKS_PER_DOC = int(os.getenv("KB_MAX_CHUNKS_PER_DOC", "16"))

LLM_BASE = os.getenv("EVIDENCE_LLM_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
LLM_KEY = os.getenv("EVIDENCE_LLM_KEY", "") or os.getenv("DASHSCOPE_API_KEY", "")
LLM_MODEL = os.getenv("EVIDENCE_LLM_MODEL", "qwen-plus")
JUDGE_MODEL = os.getenv("EVIDENCE_JUDGE_MODEL", LLM_MODEL)

ANSWER_SYSTEM = (
    "你是中国传媒大学校内制度问答助手。只能依据下面给出的知识库原文回答用户问题，"
    "不得编造或推断原文没有的具体数字、日期、地点、人名或结论。"
    "回答须标注依据的文档名与页码。若原文不足以回答问题，直接说明"
    "“知识库中没有直接回答这个问题的内容”，不要给出猜测性答案。"
)
JUDGE_SYSTEM = (
    "你是检索问答评测员。对照标准答案与要点，判断模型回答的质量，只输出 JSON，格式："
    '{"correct": true/false, "complete": true/false, "fabricated": true/false, "refused": true/false, "reason": "简短中文理由"}。'
    "correct=核心事实与标准答案一致（措辞不同但事实等价算一致）；"
    "complete=覆盖全部必答要点（无要点列表时与 correct 相同）；"
    "fabricated=回答包含给定原文中没有的具体数字/日期/结论等编造内容；"
    "refused=回答明确表示无法回答或知识库没有相关内容。"
)


def load_corpus_map():
    """chunk_id -> (doc_id, normalized text) for stage-level evidence checks."""
    data_dir = Path(os.getenv("KB_DATA_DIR", ROOT / "data"))
    corpus = {}
    with (data_dir / "chunks.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            chunk = json.loads(line)
            corpus[chunk["chunk_id"]] = (chunk["doc_id"], norm(chunk.get("text", "")))
    return corpus


def item_covered(item, results):
    """True when one returned chunk from the expected doc contains all terms."""
    terms = [norm(t) for t in item["terms"]]
    for result in results:
        if result.get("doc_id") != item["doc_id"]:
            continue
        text = norm(result.get("text", ""))
        if all(term in text for term in terms):
            return True
    return False


def item_first_rank(item, results):
    terms = [norm(t) for t in item["terms"]]
    for rank, result in enumerate(results, 1):
        if result.get("doc_id") != item["doc_id"]:
            continue
        text = norm(result.get("text", ""))
        if all(term in text for term in terms):
            return rank
    return None


def item_cover_result(item, results):
    """First result whose text covers all terms of the item (or None)."""
    terms = [norm(t) for t in item["terms"]]
    for result in results:
        if result.get("doc_id") != item["doc_id"]:
            continue
        if all(term in norm(result.get("text", "")) for term in terms):
            return result
    return None


def covered_via_expansion(item, results):
    """True when the covering result merged the item's own chunk as a neighbour.

    With KB_EXPAND_ADJACENT the anchor result's text includes +-1 active
    neighbours (expanded_chunks); an item whose chunk was merged in — rather
    than retrieved as the anchor — is evidence the expansion did the work.
    """
    if not item.get("chunk_id"):
        return False
    result = item_cover_result(item, results)
    return bool(result) and item["chunk_id"] in (result.get("expanded_chunks") or [])


def stage_evidence_present(item, chunk_ids, corpus):
    terms = [norm(t) for t in item["terms"]]
    for chunk_id in chunk_ids:
        entry = corpus.get(chunk_id)
        if entry and entry[0] == item["doc_id"] and all(term in entry[1] for term in terms):
            return True
    return False


def diagnose_item(item, trace, corpus, final_ids):
    """Deepest stage that still contains the evidence, plus the recall source."""
    dense_ids = [h["chunk_id"] for h in trace.get("dense", [])]
    bm25_ids = [h["chunk_id"] for h in trace.get("bm25", [])]
    fused_ids = [h["chunk_id"] for h in trace.get("fused", [])]
    selected = trace.get("selected", [])
    order = trace.get("order", [])
    in_dense = item["chunk_id"] in dense_ids or stage_evidence_present(item, dense_ids, corpus)
    in_bm25 = item["chunk_id"] in bm25_ids or stage_evidence_present(item, bm25_ids, corpus)
    recall_source = (
        "both" if in_dense and in_bm25 else "dense" if in_dense else "bm25" if in_bm25 else "none"
    )
    if stage_evidence_present(item, final_ids, corpus) or item["chunk_id"] in final_ids:
        loss = "ok"
    elif stage_evidence_present(item, order, corpus):
        loss = "ranking"  # selected, but ranked below the top_k cut
    elif stage_evidence_present(item, selected, corpus):
        loss = "ranking"
    elif stage_evidence_present(item, fused_ids, corpus):
        per_doc = sum(1 for cid in selected if corpus.get(cid, ("", ""))[0] == item["doc_id"])
        loss = "per_doc_cap" if per_doc >= MAX_CHUNKS_PER_DOC else "fusion_pool"
    elif in_dense or in_bm25:
        loss = "fusion_pool"  # recalled, but below the traced fusion pool cut
    else:
        loss = "recall"  # absent from both traced top-40 recall lists
    return {"loss_stage": loss, "recall_source": recall_source}


# ---- optional LLM answer generation + judging ------------------------------


def llm_chat(model, messages, cache, max_tokens=1024):
    key = hashlib.sha256(
        json.dumps({"model": model, "messages": messages}, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if key in cache:
        return cache[key]
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        LLM_BASE + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + LLM_KEY},
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
            text = body["choices"][0]["message"]["content"]
            cache[key] = text
            return text
        except (urllib.error.URLError, TimeoutError, KeyError) as exc:
            if attempt == 0:
                time.sleep(2)
                continue
            raise RuntimeError(f"LLM call failed: {exc}") from exc
    raise RuntimeError("LLM call failed")


def parse_verdict(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"correct": False, "complete": False, "fabricated": False, "refused": False,
                "_parse_failed": True,
                "reason": f"judge output not JSON: {text[:120]}"}
    try:
        verdict = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"correct": False, "complete": False, "fabricated": False, "refused": False,
                "_parse_failed": True,
                "reason": f"judge JSON invalid: {text[:120]}"}
    return {key: bool(verdict.get(key)) for key in ("correct", "complete", "fabricated", "refused")} | {
        "reason": str(verdict.get("reason", ""))[:300]
    }


def judge_with_retry(judge_user, cache, attempts=3):
    """Judge once, then retry on a parse failure with a larger token budget.

    The common failure is a verdict truncated mid-JSON (the reason text runs past
    max_tokens), so each retry both raises the budget and nudges the model to emit
    a single compact JSON object. The nudge varies per attempt so the response
    cache does not replay the same truncated output. Deterministic metrics are
    unaffected; only the optional LLM verdict pass uses this.
    """
    verdict = None
    for attempt in range(attempts):
        nudge = "" if attempt == 0 else (
            f"\n\n（第{attempt + 1}次提醒：上次输出不是合法 JSON。请把 reason 控制在 50 字以内，"
            "只输出一个合法 JSON 对象，不要输出任何其他文字。）"
        )
        messages = [{"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": judge_user + nudge}]
        verdict = parse_verdict(
            llm_chat(JUDGE_MODEL, messages, cache, max_tokens=512 + 512 * attempt)
        )
        if not verdict.get("_parse_failed"):
            break
    verdict.pop("_parse_failed", None)
    return verdict


def fragment_label(r, i):
    """Context fragment header; flags superseded docs so the answerer treats them
    as historical, mirroring the MCP citation.superseded_by guidance."""
    cit = r.get("citation", {})
    label = f"【片段{i}】《{cit.get('doc_title', '')}》第{cit.get('page_start', '?')}页"
    if cit.get("superseded_by"):
        label += "（该文件已被新版替代/废止，仅作历史参考，现行规定以未废止的新版为准）"
    return label + "：\n" + r.get("text", "")


def llm_evaluate(case, results, cache):
    context = "\n\n".join(fragment_label(r, i) for i, r in enumerate(results[:5], 1))
    messages = [{"role": "system", "content": ANSWER_SYSTEM}]
    messages.extend(case.get("history") or [])
    messages.append({"role": "user", "content": f"知识库原文：\n{context}\n\n用户问题：{case['query']}"})
    answer = llm_chat(LLM_MODEL, messages, cache)
    points = case.get("required_points") or []
    judge_user = (
        f"用户问题：{case['query']}\n标准答案：{case.get('answer', '')}\n"
        f"必答要点：{json.dumps(points, ensure_ascii=False)}\n"
        f"给定原文：\n{context}\n\n模型回答：\n{answer}"
    )
    verdict = judge_with_retry(judge_user, cache)
    return {"answer": answer, **verdict}


# ---- runner -----------------------------------------------------------------


def run_case(case, corpus, use_llm, cache):
    started = time.time()
    body = request_json(
        API, TOKEN, "/v1/search",
        {"query": case["query"], "top_k": TOP_K, "trace": True},
    )
    took_ms = (time.time() - started) * 1000
    results = body.get("results") or []
    trace = body.get("trace")
    final_ids = [r.get("chunk_id") for r in results]
    row = {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "answerable": case.get("answerable", True),
        "no_answer": bool(body.get("no_answer")),
        "confidence": body.get("confidence"),
        "degraded": bool(body.get("degraded")),
        "took_ms": round(took_ms),
        "top_title": results[0]["citation"]["doc_title"] if results else None,
        "evidence": [],
    }
    for item in case.get("evidence", []):
        entry = {
            "doc_id": item["doc_id"],
            "chunk_id": item.get("chunk_id"),
            "first_rank": item_first_rank(item, results),
            "verified_chunk_in_top": {
                str(k): item.get("chunk_id") in final_ids[:k] for k in CUTS
            },
            "covered": {str(k): item_covered(item, results[:k]) for k in CUTS},
            "via_expansion": {
                str(k): covered_via_expansion(item, results[:k]) for k in CUTS
            },
        }
        if trace is not None and item.get("chunk_id"):
            entry.update(diagnose_item(item, trace, corpus, final_ids))
        row["evidence"].append(entry)
    if case.get("watch_docs"):
        row["watch_ranks"] = {
            doc: next(
                (rank for rank, r in enumerate(results, 1) if r.get("doc_id") == doc),
                None,
            )
            for doc in case["watch_docs"]
        }
    for k in CUTS:
        row[f"evidence_hit@{k}"] = bool(row["evidence"]) and all(
            e["covered"][str(k)] for e in row["evidence"]
        )
        row[f"verified_chunk_hit@{k}"] = bool(row["evidence"]) and all(
            e["verified_chunk_in_top"][str(k)] for e in row["evidence"]
        )
        row[f"item_coverage@{k}"] = (
            round(sum(e["covered"][str(k)] for e in row["evidence"]) / len(row["evidence"]), 4)
            if row["evidence"] else None
        )
    if use_llm and case.get("answerable", True):
        row["llm"] = llm_evaluate(case, results, cache)
    return row, took_ms, trace is not None


def summarize(rows, latencies, trace_available):
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    summary = {
        "total": len(rows),
        "answerable_total": len(answerable),
        "unanswerable_total": len(unanswerable),
        "trace_available": trace_available,
        "degraded": sum(r["degraded"] for r in rows),
        "latency_p50": round(percentile(latencies, 50)),
        "latency_p95": round(percentile(latencies, 95)),
    }
    for k in CUTS:
        summary[f"evidence_hit@{k}"] = sum(r[f"evidence_hit@{k}"] for r in answerable)
        summary[f"verified_chunk_hit@{k}"] = sum(r[f"verified_chunk_hit@{k}"] for r in answerable)
        coverages = [r[f"item_coverage@{k}"] for r in answerable if r[f"item_coverage@{k}"] is not None]
        summary[f"item_coverage@{k}"] = round(sum(coverages) / len(coverages), 4) if coverages else None
    summary["false_refuse"] = [r["id"] for r in answerable if r["no_answer"]]
    summary["false_accept"] = [r["id"] for r in unanswerable if not r["no_answer"]]
    summary["supported_answer@5"] = sum(
        r["evidence_hit@5"] and not r["no_answer"] for r in answerable
    )
    losses = {}
    sources = {}
    for row in answerable:
        for entry in row["evidence"]:
            if "loss_stage" in entry:
                losses[entry["loss_stage"]] = losses.get(entry["loss_stage"], 0) + 1
                sources[entry["recall_source"]] = sources.get(entry["recall_source"], 0) + 1
    summary["evidence_loss_stages"] = losses
    summary["evidence_recall_sources"] = sources
    by_category = {}
    for row in rows:
        bucket = by_category.setdefault(row["category"], {"total": 0, "evidence_hit@5": 0, "answerable": 0})
        bucket["total"] += 1
        if row["answerable"]:
            bucket["answerable"] += 1
            bucket["evidence_hit@5"] += row["evidence_hit@5"]
    summary["by_category"] = by_category
    split_rows = [r for r in answerable if r["category"] == "split_adjacent"]
    if split_rows:
        items = [e for r in split_rows for e in r["evidence"]]
        summary["split"] = {
            "cases": len(split_rows),
            "hit@5": sum(r["evidence_hit@5"] for r in split_rows),
            "items_total": len(items),
            "items_covered_via_expansion@5": sum(e["via_expansion"]["5"] for e in items),
        }
    version_rows = [r for r in answerable if r["category"] == "version_superseded"]
    if version_rows:
        old_wins = []
        for row in version_rows:
            ranks = [v for v in (row.get("watch_ranks") or {}).values() if v is not None]
            new_ranks = [e["first_rank"] for e in row["evidence"] if e["first_rank"] is not None]
            old_rank = min(ranks) if ranks else None
            new_rank = min(new_ranks) if new_ranks else None
            if old_rank is not None and (new_rank is None or old_rank < new_rank):
                old_wins.append(row["id"])
        summary["version"] = {
            "cases": len(version_rows),
            "hit@5": sum(r["evidence_hit@5"] for r in version_rows),
            "old_above_new": old_wins,
        }
    llm_rows = [r for r in answerable if "llm" in r]
    if llm_rows:
        summary["llm"] = {
            "total": len(llm_rows),
            "correct": sum(r["llm"]["correct"] for r in llm_rows),
            "complete": sum(r["llm"]["complete"] for r in llm_rows),
            "fabricated": sum(r["llm"]["fabricated"] for r in llm_rows),
            "refused": sum(r["llm"]["refused"] for r in llm_rows),
        }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dataset", default=str(ROOT / "eval/heldout/dataset_evidence_v1.json"))
    parser.add_argument("--category", help="只跑某个类别")
    parser.add_argument("--ids", help="逗号分隔的 case id，调试用")
    parser.add_argument("--limit", type=int, help="只跑前 N 题，试跑用")
    parser.add_argument("--llm", action="store_true", help="附加 LLM 作答+判定（需 EVIDENCE_LLM_KEY）")
    args = parser.parse_args()
    if not TOKEN:
        sys.exit("set KB_API_TOKEN")
    if args.llm and not LLM_KEY:
        sys.exit("--llm requires EVIDENCE_LLM_KEY (or DASHSCOPE_API_KEY)")

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    cases = dataset["cases"]
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]

    corpus = load_corpus_map()
    cache_path = ROOT / f"eval/heldout/_llm_cache_{args.tag}.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if args.llm and cache_path.exists() else {}

    rows, latencies = [], []
    trace_available = True
    for case in cases:
        row, took_ms, has_trace = run_case(case, corpus, args.llm, cache)
        trace_available = trace_available and has_trace
        rows.append(row)
        latencies.append(took_ms)
        hits = " ".join(f"@{k}={int(row[f'evidence_hit@{k}'])}" for k in CUTS)
        stages = ",".join(e.get("loss_stage", "?") for e in row["evidence"]) or "-"
        print(
            f"{case['id']} [{case['category']}] na={row['no_answer']} {hits} loss={stages}",
            flush=True,
        )
        if args.llm:
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    summary = {"tag": args.tag, "dataset": Path(args.dataset).name, **summarize(rows, latencies, trace_available)}
    out = ROOT / f"eval/heldout/results_evidence_{args.tag}.json"
    out.write_text(
        json.dumps({"summary": summary, "details": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"saved {out}")


if __name__ == "__main__":
    main()