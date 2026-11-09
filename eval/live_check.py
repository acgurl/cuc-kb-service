#!/usr/bin/env python3
"""Post-deploy live-API check on the held-out + no-answer sets.

Runs on Phoenix against the loopback API (no tuning on these sets):

  KB_API_TOKEN=$(grep '^KB_API_TOKEN=' .env | cut -d= -f2-) \
  .venv/bin/python eval/live_check.py --tag qwen37_v3 [--limit 0]

Metrics mirror the 2026-11-01 baseline: curated hit@1/@3/@5 + MRR (no assistant),
no-answer rejection on heldout(10) + noanswer_v1 strict(45), partial(10) reported
informationally. Results saved to eval/ab/results_<tag>.json.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = os.getenv("KB_API_URL", "http://127.0.0.1:8000")
TOKEN = os.getenv("KB_API_TOKEN", "")


def search(query):
    payload = json.dumps({"query": query, "top_k": 5}).encode("utf-8")
    req = urllib.request.Request(
        API + "/v1/search",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN},
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=90) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body, (time.time() - started) * 1000


def doc_rank(results, expected):
    for rank, item in enumerate(results, 1):
        title = (item.get("citation") or {}).get("doc_title", "")
        if any(e in title for e in expected):
            return rank
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if not TOKEN:
        sys.exit("set KB_API_TOKEN")

    heldout = json.loads((ROOT / "eval/heldout/dataset_heldout_v1.json").read_text(encoding="utf-8"))
    na_set = json.loads((ROOT / "eval/heldout/dataset_noanswer_v1.json").read_text(encoding="utf-8"))
    curated = heldout["curated"][: args.limit] if args.limit else heldout["curated"]

    rows = []
    hits = {1: 0, 3: 0, 5: 0}
    mrr_sum = 0.0
    answered = 0
    latencies = []
    for case in curated:
        body, ms = search(case["query"])
        latencies.append(ms)
        results = body.get("results") or []
        rank = doc_rank(results, case["expected_docs"])
        for k in hits:
            if rank is not None and rank <= k:
                hits[k] += 1
        if rank:
            mrr_sum += 1.0 / rank
        if not body.get("no_answer"):
            answered += 1
        rows.append({"id": case["id"], "query": case["query"], "rank": rank,
                     "no_answer": body.get("no_answer"), "took_ms": round(ms),
                     "top_title": (results[0].get("citation") or {}).get("doc_title") if results else None})
        print(f"{case['id']} rank={rank} no_answer={body.get('no_answer')} {ms:.0f}ms", flush=True)

    na_rows = []
    na_cases = [(c["id"], c["query"], "heldout") for c in heldout["no_answer"]]
    na_cases += [(c["id"], c["query"], "strict") for c in na_set["no_answer"]]
    na_cases += [(c["id"], c["query"], "partial") for c in na_set.get("partial", [])]
    rejected = {"heldout": 0, "strict": 0}
    totals = {"heldout": 0, "strict": 0}
    for cid, query, kind in na_cases:
        body, ms = search(query)
        latencies.append(ms)
        no_answer = bool(body.get("no_answer"))
        if kind in totals:
            totals[kind] += 1
            if no_answer:
                rejected[kind] += 1
        na_rows.append({"id": cid, "kind": kind, "query": query, "no_answer": no_answer,
                        "took_ms": round(ms)})
        print(f"{cid}[{kind}] no_answer={no_answer} {ms:.0f}ms", flush=True)

    ordered = sorted(latencies)
    summary = {
        "tag": args.tag,
        "curated": len(curated),
        "hit@1": hits[1], "hit@3": hits[3], "hit@5": hits[5],
        "mrr": round(mrr_sum / len(curated), 4) if curated else None,
        "answered": answered,
        "no_answer_rejected": rejected,
        "no_answer_totals": totals,
        "partial_refused": sum(1 for r in na_rows if r["kind"] == "partial" and r["no_answer"]),
        "partial_total": sum(1 for r in na_rows if r["kind"] == "partial"),
        "latency_p50_ms": round(ordered[len(ordered) // 2]),
        "latency_p95_ms": round(ordered[int(len(ordered) * 0.95)]),
    }
    out = ROOT / f"eval/ab/results_{args.tag}.json"
    out.write_text(json.dumps({"summary": summary, "curated_rows": rows, "no_answer_rows": na_rows},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"saved {out}")


if __name__ == "__main__":
    main()