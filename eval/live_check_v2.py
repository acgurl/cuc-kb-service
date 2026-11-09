#!/usr/bin/env python3
"""Post-deploy live-API check on the held-out v2 + no-answer v2 sets.

Runs on Phoenix against the loopback API (no tuning on these sets):

  KB_API_TOKEN=$(grep '^KB_API_TOKEN=' .env | cut -d= -f2-) \
  .venv/bin/python eval/live_check_v2.py --tag v4_20261108 [--set both]

Mirrors the 2026-11-07 v2 baseline metrics (results_heldout_v2.json):
curated hit@1/@3/@5/@10 + keyword@5 + MRR, short answered/hit, isolation
leak via docs.json assistant map, citation page range, no-answer rejection.
Also re-validates no-answer probes against the current active corpus and
reports cases invalidated by newly added documents.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
from kbeval import doc_rank, keyword_hit, norm, percentile, search  # noqa: E402

API = os.getenv("KB_API_URL", "http://127.0.0.1:8000")
TOKEN = os.getenv("KB_API_TOKEN", "")


def load_docs_map():
    data_dir = Path(os.getenv("KB_DATA_DIR", ROOT / "data"))
    docs = json.loads((data_dir / "docs.json").read_text(encoding="utf-8"))
    return {d["doc_id"]: (d.get("assistants") or []) for d in docs}


def active_texts():
    data_dir = Path(os.getenv("KB_DATA_DIR", ROOT / "data"))
    texts = []
    with (data_dir / "chunks.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            chunk = json.loads(line)
            if chunk.get("status") == "active":
                texts.append(norm(chunk.get("text", "")) + "\n" + norm(chunk.get("doc_title", "")))
    return texts


def run_curated(cases, doc_map, latencies, counters, label):
    rows = []
    hits = {1: 0, 3: 0, 5: 0, 10: 0}
    kw = 0
    mrr = 0.0
    answered = 0
    for case in cases:
        t0 = time.time()
        body = search(API, TOKEN, case["query"], top_k=10)
        latencies.append((time.time() - t0) * 1000)
        results = body.get("results") or []
        if any(r.get("rerank_score") is not None for r in results):
            counters["rerank_used"] += 1
        rank = doc_rank(results, case["expected_docs"])
        for k in hits:
            if rank is not None and rank <= k:
                hits[k] += 1
        if rank:
            mrr += 1.0 / rank
        if keyword_hit(results, case.get("keywords", []), top=5):
            kw += 1
        if not body.get("no_answer"):
            answered += 1
        rows.append({
            "id": case["id"], "query": case["query"], "rank": rank,
            "no_answer": body.get("no_answer"),
            "keyword@5": keyword_hit(results, case.get("keywords", []), top=5),
            "top_title": results[0]["citation"]["doc_title"] if results else None,
        })
        print(f"{case['id']} rank={rank} na={body.get('no_answer')}", flush=True)
    n = len(cases)
    return rows, {
        f"{label}_total": n,
        f"{label}_hit@1": hits[1], f"{label}_hit@3": hits[3],
        f"{label}_hit@5": hits[5], f"{label}_hit@10": hits[10],
        f"{label}_keyword@5": kw,
        f"{label}_mrr": round(mrr / n, 4) if n else 0.0,
        f"{label}_answered": answered,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--set", choices=["heldout", "noanswer", "both"], default="both")
    args = parser.parse_args()
    if not TOKEN:
        sys.exit("set KB_API_TOKEN")

    heldout = json.loads((ROOT / "eval/heldout/dataset_heldout_v2.json").read_text(encoding="utf-8"))
    na_set = json.loads((ROOT / "eval/heldout/dataset_noanswer_v2.json").read_text(encoding="utf-8"))
    doc_map = load_docs_map()
    latencies = []
    counters = {"rerank_used": 0}
    summary = {"tag": args.tag}
    details = {}

    if args.set in ("heldout", "both"):
        rows, s = run_curated(heldout["curated"], doc_map, latencies, counters, "curated")
        summary.update(s)
        details["curated"] = rows
        rows, s = run_curated(heldout["short"], doc_map, latencies, counters, "short")
        summary.update({k: v for k, v in s.items() if k != "short_answered"})
        summary["short_answered"] = s["short_answered"]
        details["short"] = rows

        iso_rows = []
        iso_pass = iso_hit = iso_leak = 0
        for case in heldout["isolation"]:
            assistant = case["assistant"]
            t0 = time.time()
            body = search(API, TOKEN, case["query"], assistant=assistant, top_k=5)
            latencies.append((time.time() - t0) * 1000)
            results = body.get("results") or []
            rank = doc_rank(results, case["expected_docs"])
            leak = [
                r["doc_id"] for r in results
                if r["doc_id"] in doc_map
                and assistant not in doc_map[r["doc_id"]]
                and "shared" not in doc_map[r["doc_id"]]
            ]
            ok = rank is not None and not leak
            iso_pass += ok
            iso_hit += rank is not None
            iso_leak += len(leak)
            iso_rows.append({"id": case["id"], "rank": rank, "leak": leak, "pass": ok})
            print(f"{case['id']} iso rank={rank} leak={len(leak)}", flush=True)
        summary.update({
            "isolation_total": len(heldout["isolation"]),
            "isolation_pass": iso_pass, "isolation_hit": iso_hit, "isolation_leak": iso_leak,
        })
        details["isolation"] = iso_rows

        cit_rows = []
        cit_pass = 0
        for case in heldout["citation"]:
            t0 = time.time()
            body = search(API, TOKEN, case["query"], top_k=5)
            latencies.append((time.time() - t0) * 1000)
            results = body.get("results") or []
            page = case["expected_page"]
            ok = False
            for r in results:
                cit = r.get("citation") or {}
                title = cit.get("doc_title", "")
                if any(e in title for e in case["expected_docs"]):
                    if cit.get("page_start", 0) <= page <= max(cit.get("page_end", 0), cit.get("page_start", 0)):
                        ok = True
                        break
            cit_pass += ok
            cit_rows.append({"id": case["id"], "pass": ok, "expected_page": page})
            print(f"{case['id']} cite page={page} pass={ok}", flush=True)
        summary.update({"citation_total": len(heldout["citation"]), "citation_pass": cit_pass})
        details["citation"] = cit_rows

    if args.set in ("noanswer", "both"):
        texts = active_texts()
        invalidated = []
        for case in na_set["no_answer"]:
            probes = [norm(p) for p in case.get("probes", [])]
            if any(p in h for p in probes for h in texts):
                invalidated.append(case["id"])
        na_rows = []
        na_pass = 0
        for case in na_set["no_answer"]:
            t0 = time.time()
            body = search(API, TOKEN, case["query"], top_k=5)
            latencies.append((time.time() - t0) * 1000)
            results = body.get("results") or []
            if any(r.get("rerank_score") is not None for r in results):
                counters["rerank_used"] += 1
            rejected = bool(body.get("no_answer"))
            na_pass += rejected
            na_rows.append({
                "id": case["id"], "query": case["query"], "no_answer": rejected,
                "confidence": body.get("confidence"),
                "invalidated_by_new_corpus": case["id"] in invalidated,
                "top_title": results[0]["citation"]["doc_title"] if results else None,
            })
            print(f"{case['id']} na={rejected} conf={body.get('confidence')}", flush=True)
        partial_rows = []
        partial_refused = 0
        for case in na_set.get("partial", []):
            t0 = time.time()
            body = search(API, TOKEN, case["query"], top_k=5)
            latencies.append((time.time() - t0) * 1000)
            rejected = bool(body.get("no_answer"))
            partial_refused += rejected
            partial_rows.append({"id": case["id"], "no_answer": rejected, "confidence": body.get("confidence")})
            print(f"{case['id']}[partial] na={rejected}", flush=True)
        valid_total = len(na_set["no_answer"]) - len(invalidated)
        valid_pass = sum(1 for r in na_rows if r["no_answer"] and not r["invalidated_by_new_corpus"])
        summary.update({
            "no_answer_total": len(na_set["no_answer"]),
            "no_answer_pass": na_pass,
            "no_answer_invalidated": invalidated,
            "no_answer_valid_total": valid_total,
            "no_answer_valid_pass": valid_pass,
            "partial_total": len(partial_rows),
            "partial_refused": partial_refused,
        })
        details["no_answer"] = na_rows
        details["partial"] = partial_rows

    summary["latency_p50"] = round(percentile(latencies, 50))
    summary["latency_p95"] = round(percentile(latencies, 95))
    summary["rerank_used"] = counters["rerank_used"]
    out = ROOT / f"eval/heldout/results_{args.tag}.json"
    out.write_text(json.dumps({"summary": summary, "details": details}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"saved {out}")


if __name__ == "__main__":
    main()