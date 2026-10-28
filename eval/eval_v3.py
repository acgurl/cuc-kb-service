#!/usr/bin/env python3
import argparse
import json
import os
import re
import statistics
import time
import urllib.request
from pathlib import Path


def norm(text):
    return re.sub(r"\s+", "", text or "")


def request_json(api, token, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        api.rstrip("/") + path,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def percentile(values, pct):
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]


def doc_rank(results, expected):
    for rank, item in enumerate(results, 1):
        title = item.get("citation", {}).get("doc_title", "")
        if any(e in title for e in expected):
            return rank
    return None


def keyword_hit(results, keywords):
    texts = [norm(item.get("text", "")) for item in results]
    return any(norm(k) in text for k in keywords for text in texts)


def source_assistant(item):
    source = item.get("citation", {}).get("source_file", "")
    return source.split("/")[0] if "/" in source else source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default=os.getenv("KB_API_TOKEN", ""))
    parser.add_argument("--data", default="eval/dataset_v3.json")
    parser.add_argument("--docs", default="data/docs.json")
    parser.add_argument("--out", default="/tmp/eval_v3")
    parser.add_argument("--tag", default="dev")
    parser.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("KB_API_TOKEN is required")
    dataset = json.loads(Path(args.data).read_text(encoding="utf-8"))
    docs = {d["doc_id"]: d for d in json.loads(Path(args.docs).read_text(encoding="utf-8"))}
    docs["qa-csv"] = {"assistants": ["中传教务君", "问道中传"]}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    def selected(cases):
        if args.split == "all":
            return cases
        return [case for case in cases if case.get("split", "dev") == args.split]

    def call(payload):
        return request_json(args.api, args.token, "/v1/search", payload)

    details = {"curated": [], "short": [], "no_answer": [], "isolation": [], "citation": []}
    latencies = []
    rerank_used = 0

    curated = selected(dataset.get("curated", []))
    hits = {1: 0, 3: 0, 5: 0, 10: 0}
    keyword_hits = 0
    mrr = 0.0
    for case in curated:
        response = call({"query": case["query"], "top_k": args.top_k})
        results = response["results"]
        latencies.append(response["took_ms"])
        if any(item.get("rerank_score") is not None for item in results):
            rerank_used += 1
        rank = doc_rank(results, case["expected_docs"])
        for k in hits:
            hits[k] += 1 if rank and rank <= k else 0
        if rank:
            mrr += 1.0 / rank
        khit = keyword_hit(results, case["keywords"])
        keyword_hits += 1 if khit else 0
        details["curated"].append(
            {
                "id": case["id"], "dept": case["dept"], "query": case["query"],
                "expected_docs": case["expected_docs"], "keywords": case["keywords"],
                "doc_rank": rank, "keyword_hit": khit,
                "no_answer": response["no_answer"], "confidence": response["confidence"],
                "top_title": results[0]["citation"]["doc_title"] if results else None,
                "top_text": results[0]["text"][:240] if results else "",
                "took_ms": response["took_ms"],
            }
        )

    short = selected(dataset.get("short", []))
    short_hits = {1: 0, 3: 0, 5: 0}
    short_keyword_hits = 0
    short_answered = 0
    short_na_total = 0
    short_na_pass = 0
    for case in short:
        response = call({"query": case["query"], "top_k": args.top_k})
        results = response["results"]
        latencies.append(response["took_ms"])
        if any(item.get("rerank_score") is not None for item in results):
            rerank_used += 1
        if case.get("expect_no_answer"):
            short_na_total += 1
            short_na_pass += 1 if response["no_answer"] else 0
            rank = None
            khit = False
        else:
            rank = doc_rank(results, case.get("expected_docs", []))
            for k in short_hits:
                short_hits[k] += 1 if rank and rank <= k else 0
            khit = keyword_hit(results, case.get("keywords", []))
            short_keyword_hits += 1 if khit else 0
            short_answered += 1 if (not response["no_answer"] and results) else 0
        details["short"].append(
            {
                "id": case["id"], "dept": case.get("dept", ""), "query": case["query"],
                "doc_rank": rank, "keyword_hit": khit,
                "no_answer": response["no_answer"], "confidence": response["confidence"],
                "top_title": results[0]["citation"]["doc_title"] if results else None,
                "took_ms": response["took_ms"],
            }
        )

    no_answer = selected(dataset.get("no_answer", []))
    na_pass = 0
    for case in no_answer:
        response = call({"query": case["query"], "top_k": args.top_k})
        results = response["results"]
        latencies.append(response["took_ms"])
        if any(item.get("rerank_score") is not None for item in results):
            rerank_used += 1
        passed = bool(response["no_answer"])
        na_pass += 1 if passed else 0
        details["no_answer"].append(
            {
                "id": case["id"], "query": case["query"], "passed": passed,
                "no_answer": response["no_answer"], "confidence": response["confidence"],
                "top_title": results[0]["citation"]["doc_title"] if results else None,
                "took_ms": response["took_ms"],
            }
        )

    isolation = selected(dataset.get("isolation", []))
    iso_pass = 0
    iso_hit = 0
    iso_leak = 0
    for case in isolation:
        response = call(
            {"query": case["query"], "assistant": case["assistant"], "top_k": args.top_k}
        )
        results = response["results"]
        latencies.append(response["took_ms"])
        if any(item.get("rerank_score") is not None for item in results):
            rerank_used += 1
        rank = doc_rank(results, case["expected_docs"])
        hit = bool(rank and rank <= 5)
        leaks = []
        for item in results:
            meta = docs.get(item.get("doc_id"), {})
            assistants = meta.get("assistants", [])
            if case["assistant"] not in assistants and "shared" not in assistants:
                leaks.append(item.get("doc_id"))
        leak = bool(leaks)
        passed = hit and not leak and not response["no_answer"]
        iso_pass += 1 if passed else 0
        iso_hit += 1 if hit else 0
        iso_leak += 1 if leak else 0
        details["isolation"].append(
            {
                "id": case["id"], "assistant": case["assistant"], "query": case["query"],
                "expected_docs": case["expected_docs"], "doc_rank": rank,
                "hit": hit, "leak": leak, "leak_ids": leaks,
                "no_answer": response["no_answer"],
                "titles": [item["citation"]["doc_title"] for item in results[:5]],
                "took_ms": response["took_ms"],
            }
        )

    citation = selected(dataset.get("citation", []))
    cit_pass = 0
    for case in citation:
        response = call({"query": case["query"], "top_k": args.top_k})
        results = response["results"]
        latencies.append(response["took_ms"])
        if any(item.get("rerank_score") is not None for item in results):
            rerank_used += 1
        expected_page = int(case["expected_page"])
        found = None
        page_ok = False
        page_range = None
        for item in results[:5]:
            title = item.get("citation", {}).get("doc_title", "")
            if not any(e in title for e in case["expected_docs"]):
                continue
            citation_meta = item["citation"]
            start = int(citation_meta.get("page_start") or 0)
            end = int(citation_meta.get("page_end") or start)
            if found is None:
                found = item
                page_range = [start, end]
            if start and start <= expected_page <= end:
                found = item
                page_range = [start, end]
                page_ok = True
                break
        passed = bool(found and page_ok)
        cit_pass += 1 if passed else 0
        details["citation"].append(
            {
                "id": case["id"], "query": case["query"], "expected_docs": case["expected_docs"],
                "expected_page": expected_page, "found_title": found["citation"]["doc_title"] if found else None,
                "page_range": page_range, "page_ok": page_ok, "passed": passed,
                "no_answer": response["no_answer"], "took_ms": response["took_ms"],
            }
        )

    summary = {
        "tag": args.tag,
        "split": args.split,
        "curated_total": len(curated),
        "curated_hit@1": hits[1],
        "curated_hit@3": hits[3],
        "curated_hit@5": hits[5],
        "curated_hit@10": hits[10],
        "curated_keyword@5": keyword_hits,
        "curated_mrr": round(mrr / max(len(curated), 1), 4),
        "short_total": len(short),
        "short_answered": short_answered,
        "short_hit@1": short_hits[1],
        "short_hit@3": short_hits[3],
        "short_hit@5": short_hits[5],
        "short_keyword@5": short_keyword_hits,
        "short_na_pass": short_na_pass,
        "short_na_total": short_na_total,
        "no_answer_total": len(no_answer),
        "no_answer_pass": na_pass,
        "isolation_total": len(isolation),
        "isolation_pass": iso_pass,
        "isolation_hit": iso_hit,
        "isolation_leak": iso_leak,
        "citation_total": len(citation),
        "citation_pass": cit_pass,
        "latency_p50": percentile(latencies, 50),
        "latency_p95": percentile(latencies, 95),
        "rerank_used": rerank_used,
    }

    (out_dir / ("results_%s.json" % args.tag)).write_text(
        json.dumps(
            {"summary": summary, "details": details},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        "# CUC KB Eval v3 — %s (%s)" % (args.tag, args.split),
        "",
        "| 指标 | 结果 |",
        "|---|---|",
        "| 跨部门文档命中@1 | %d/%d |" % (hits[1], len(curated)),
        "| 跨部门文档命中@3 | %d/%d |" % (hits[3], len(curated)),
        "| 跨部门文档命中@5 | %d/%d |" % (hits[5], len(curated)),
        "| 跨部门关键词命中@5 | %d/%d |" % (keyword_hits, len(curated)),
        "| 跨部门 MRR | %.4f |" % (mrr / max(len(curated), 1)),
        "| 短问句回答率 | %d/%d |" % (short_answered, len(short)),
        "| 短问句命中@5 | %d/%d |" % (short_hits[5], len(short)),
        "| 短问句关键词@5 | %d/%d |" % (short_keyword_hits, len(short)),
        "| 无答案准确率 | %d/%d |" % (na_pass, len(no_answer)),
        "| 助手隔离通过 | %d/%d |" % (iso_pass, len(isolation)),
        "| 助手隔离命中 | %d/%d |" % (iso_hit, len(isolation)),
        "| 页码引用正确 | %d/%d |" % (cit_pass, len(citation)),
        "| P50 / P95 | %s / %s ms |" % (summary["latency_p50"], summary["latency_p95"]),
        "| 触发重排查询数 | %d |" % rerank_used,
        "",
        "## Bad cases",
    ]
    for case in details["curated"]:
        if not case["doc_rank"] or case["doc_rank"] > 5 or not case["keyword_hit"]:
            lines.append(
                "- [%s][%s] %s | rank=%s | kw=%s | top=%s"
                % (case["id"], case["dept"], case["query"], case["doc_rank"], case["keyword_hit"], case["top_title"])
            )
    for case in details["short"]:
        if not case.get("expect_no_answer") and (not case["doc_rank"] or case["doc_rank"] > 5):
            lines.append(
                "- [%s][短] %s | rank=%s | top=%s"
                % (case["id"], case["query"], case["doc_rank"], case["top_title"])
            )
    for case in details["no_answer"]:
        if not case["passed"]:
            lines.append(
                "- [%s][无答案] %s | conf=%s | top=%s"
                % (case["id"], case["query"], case["confidence"], case["top_title"])
            )
    for case in details["isolation"]:
        if not (case["hit"] and not case["leak"] and not case["no_answer"]):
            lines.append(
                "- [%s][隔离] %s | assistant=%s | rank=%s | leak=%s | no_answer=%s | titles=%s"
                % (case["id"], case["query"], case["assistant"], case["doc_rank"], case["leak"], case["no_answer"], case["titles"][:3])
            )
    for case in details["citation"]:
        if not case["passed"]:
            lines.append(
                "- [%s][页码] %s | expected=%s | found=%s | range=%s"
                % (case["id"], case["query"], case["expected_page"], case["found_title"], case["page_range"])
            )
    (out_dir / ("report_%s.md" % args.tag)).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("report:", out_dir / ("report_%s.md" % args.tag))


if __name__ == "__main__":
    main()
