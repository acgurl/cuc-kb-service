#!/usr/bin/env python3
"""Score the expanded no-answer set and re-check the rerank threshold.

Runs eval/heldout/dataset_noanswer_v1.json through the live configuration (bge-small +
qwen3-rerank always, no answerability override) and combines it with the answerable
and old no-answer rows of a previous ablate.py run (`base` variant).

    python eval/ab/noanswer_eval.py --base eval/ab/results_abl1.json --tag na1
"""
import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ab_embed  # noqa: E402
from ab_embed import AB_DIR, OUT_DIR, ROOT, OfflineKB, best_rerank, load_backend, load_chunks  # noqa: E402

import search_core  # noqa: E402

THRESHOLDS = [round(0.60 + 0.01 * i, 2) for i in range(26)]


def rates(ans, na, t):
    return sum(s >= t for s in ans), sum(s < t for s in na)


def auc(ans, na):
    return sum((a > n) + 0.5 * (a == n) for a in ans for n in na) / (len(ans) * len(na))


def best_threshold(ans, na):
    # Balanced accuracy; ties go to the lower threshold (keep answers).
    return max(THRESHOLDS, key=lambda t: (rates(ans, na, t)[0] / len(ans) + rates(ans, na, t)[1] / len(na), -t))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=str(OUT_DIR / "results_abl1.json"))
    parser.add_argument("--tag", default="na1")
    args = parser.parse_args()

    search_core.ANSWERABILITY_OVERRIDE = False
    search_core.RERANK_MIN_SCORE = 0.75
    chunks = load_chunks()
    kb = OfflineKB(load_backend("bge-small-zh"), np.load(AB_DIR / "bge-small-zh.npy"))
    kb.load()
    kb.active_reranker = ab_embed.RERANKERS["bl-qwen3-rerank"]()
    kb.rerank_mode = "always"

    dataset = json.loads((ROOT / "eval/heldout/dataset_noanswer_v1.json").read_text(encoding="utf-8"))
    new_rows, partial_rows = [], []
    for case in dataset["no_answer"] + dataset.get("partial", []):
        response = kb.search(case["query"], top_k=5)
        top = response["results"][0] if response["results"] else None
        (partial_rows if "review" in case else new_rows).append({
            "set": "na_v1", "id": case["id"], "category": case["category"], "query": case["query"],
            "type": "no_answer", "best_rerank": best_rerank(response["results"]),
            "no_answer": response["no_answer"],
            "top": top["citation"]["doc_title"] if top else None,
            "top_text": top["text"][:300] if top else None,
        })

    base_rows = json.loads(Path(args.base).read_text(encoding="utf-8"))["base"]["rows"]
    ans = [r["best_rerank"] for r in base_rows if r["type"] == "answerable"]
    old_na = [r["best_rerank"] for r in base_rows if r["type"] == "no_answer"]
    new_na = [r["best_rerank"] for r in new_rows]
    all_na = old_na + new_na

    print(f"answerable={len(ans)} old_no_answer={len(old_na)} new_no_answer={len(new_na)}")
    print(f"AUC answerable vs old {auc(ans, old_na):.4f} | vs new {auc(ans, new_na):.4f} | vs all {auc(ans, all_na):.4f}")
    print("thr   keep/204  rej_old/20  rej_new  rej_all")
    for t in THRESHOLDS:
        k, ro = rates(ans, old_na, t)
        _, rn = rates(ans, new_na, t)
        print(f"{t:.2f}  {k:>4}       {ro:>3}        {rn:>3}/{len(new_na)}  {ro + rn:>3}/{len(all_na)}")

    # Repeated 2-fold CV over all answerable + no-answer scores.
    rng = random.Random(0)
    picks, test_keep, test_rej = [], [], []
    for _ in range(200):
        a, n = ans[:], all_na[:]
        rng.shuffle(a); rng.shuffle(n)
        for fold in (0, 1):
            ca, cn = a[fold::2], n[fold::2]
            ta, tn = a[1 - fold::2], n[1 - fold::2]
            t = best_threshold(ca, cn)
            picks.append(t)
            k, r = rates(ta, tn, t)
            test_keep.append(k / len(ta)); test_rej.append(r / len(tn))
    picks.sort()
    print(f"2-fold CV x200: chosen thr median {picks[len(picks)//2]:.2f} (p10 {picks[len(picks)//10]:.2f}, p90 {picks[9*len(picks)//10]:.2f}); "
          f"test keep {np.mean(test_keep):.3f}, test reject {np.mean(test_rej):.3f}")

    print("\nper category @0.75:")
    for cat in sorted({r["category"] for r in new_rows}):
        rows = [r for r in new_rows if r["category"] == cat]
        print(f"  {cat:9} {sum(r['best_rerank'] < 0.75 for r in rows)}/{len(rows)}  max {max(r['best_rerank'] for r in rows):.3f}")
    print("\nhighest-scoring new no-answer questions:")
    for r in sorted(new_rows, key=lambda r: -r["best_rerank"])[:15]:
        print(f"  {r['best_rerank']:.3f} {r['id']} [{r['category']}] {r['query']}  -> {(r['top'] or '')[:40]}")

    print("\npartial (excluded from calibration):")
    for r in sorted(partial_rows, key=lambda r: -r["best_rerank"]):
        print(f"  {r['best_rerank']:.3f} {r['id']} {'answered' if not r['no_answer'] else 'refused'} {r['query']}")

    out = OUT_DIR / f"results_{args.tag}.json"
    out.write_text(json.dumps({"rows": new_rows, "partial_rows": partial_rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
