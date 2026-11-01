#!/usr/bin/env python3
"""No-answer threshold per reranker: calibrate on v5, test on the held-out set.

Uses the best rerank score among the top-5 results of the `always` mode, so every
query has a score. A query is rejected when best_rerank < threshold.

    python eval/ab/calibrate_no_answer.py eval/ab/results_rr1.json
"""
import json
import sys


def split(rows, set_name):
    ans = [r["best_rerank"] for r in rows if r["set"] == set_name and r["type"] == "answerable"]
    na = [r["best_rerank"] for r in rows if r["set"] == set_name and r["type"] == "no_answer"]
    return ans, na


def rates(ans, na, threshold):
    kept = sum(1 for s in ans if s >= threshold)
    rejected = sum(1 for s in na if s < threshold)
    return kept, rejected


def auc(ans, na):
    pairs = [(a > n) + 0.5 * (a == n) for a in ans for n in na]
    return sum(pairs) / len(pairs) if pairs else float("nan")


def main():
    report = json.load(open(sys.argv[1], encoding="utf-8"))
    print(f"{'config':32} {'thr(v5)':>8} {'v5 keep':>9} {'v5 rej':>7} | {'ho keep':>9} {'ho rej':>7} {'ho AUC':>7} | {'@0.75 ho keep/rej':>18}")
    for key, value in report.items():
        if "always" not in value:
            continue
        rows = value["always"]["rows"]
        v5_ans, v5_na = split(rows, "v5")
        ho_ans, ho_na = split(rows, "heldout")
        candidates = sorted(set(v5_ans + v5_na))
        # Balanced accuracy on v5; ties broken toward the lower threshold (keep answers).
        best = max(
            candidates,
            key=lambda t: (rates(v5_ans, v5_na, t)[0] / len(v5_ans) + rates(v5_ans, v5_na, t)[1] / len(v5_na), -t),
        )
        k5, r5 = rates(v5_ans, v5_na, best)
        kh, rh = rates(ho_ans, ho_na, best)
        k75, r75 = rates(ho_ans, ho_na, 0.75)
        print(
            f"{key:32} {best:8.4f} {k5:>4}/{len(v5_ans):<4} {r5:>3}/{len(v5_na):<3} | "
            f"{kh:>4}/{len(ho_ans):<4} {rh:>3}/{len(ho_na):<3} {auc(ho_ans, ho_na):7.3f} | "
            f"{k75:>8}/{len(ho_ans)} {r75:>3}/{len(ho_na)}"
        )


if __name__ == "__main__":
    main()
