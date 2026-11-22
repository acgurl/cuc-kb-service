"""Paired Phoenix retrieval A/B against a baseline and a candidate API."""

import argparse
import json
import math
import os
import statistics
from pathlib import Path
from urllib.request import Request, urlopen


def search(base, query, prefer_current):
    request = Request(
        base.rstrip("/") + "/v1/search",
        data=json.dumps({"query": query, "top_k": 5, "prefer_current": prefer_current}).encode(),
        headers={"Authorization": "Bearer " + os.environ["KB_API_TOKEN"], "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=18) as response:
        return json.load(response)


def metrics(rows, side):
    metrics = {}
    for kind in ("current", "historical", "comparison", "control", "no_answer"):
        cases = [row for row in rows if row["case"]["kind"] == kind]
        counts = dict(total=len(cases), hit_at_1=0, hit_at_5=0, watch_at_5=0, no_answer=0, version_review=0, degraded=0)
        for row in cases:
            case, result = row["case"], row[side]
            chunks = [{item["chunk_id"], *item.get("expanded_chunks", [])} for item in result["results"]]
            groups = [set(group) for group in (case.get("groups") or [case["gold"]])]
            available = set().union(*chunks)
            counts["hit_at_1"] += bool(chunks and all(chunks[0] & group for group in groups))
            counts["hit_at_5"] += all(available & group for group in groups)
            counts["watch_at_5"] += bool(available & set(case.get("old", [])))
            counts["no_answer"] += result["no_answer"]
            counts["version_review"] += bool(result.get("version_review"))
            counts["degraded"] += bool(result.get("degraded"))
        metrics[kind] = counts
    times = sorted(row[side]["took_ms"] for row in rows)
    metrics["latency_ms"] = dict(p50=statistics.median(times), p95=times[math.ceil(.95 * len(times)) - 1])
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="http://127.0.0.1:8000")
    parser.add_argument("--candidate", default="http://127.0.0.1:8105")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warm", action="store_true", help="Warm the query embedding cache for each case")
    args = parser.parse_args()
    cases = json.loads(args.dataset.read_text(encoding="utf-8"))
    endpoints = {"baseline": (args.baseline, False), "candidate": (args.candidate, True)}
    rows = []
    for i, case in enumerate(cases):
        row = {"case": case}
        order = list(endpoints)[::1 if i % 2 == 0 else -1]
        if args.warm:
            for name in order:
                search(endpoints[name][0], case["query"], endpoints[name][1])
        for name in order:
            base, prefer = endpoints[name]
            row[name] = search(base, case["query"], prefer)
        rows.append(row)
    summary = {name: metrics(rows, name) for name in endpoints}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    args.output.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
