#!/usr/bin/env python3
"""Ablate hand-tuned ranking rules and candidate sizes under the live qwen3-rerank setup.

Every variant runs bge-small-zh + Bailian qwen3-rerank in "always" mode with the
answerability override off (= Phoenix .env since 2026-11-01). Only the named knob
changes; everything else is production search_core code.

    python eval/ab/ablate.py --variants base,no_post_bonus --tag abl1
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ab_embed  # noqa: E402
from ab_embed import AB_DIR, OUT_DIR, OfflineKB, evaluate, load_backend, load_cases, load_chunks, summarize  # noqa: E402

from kb import search_core  # noqa: E402

# name -> {search_core constant overrides}. The post-rerank phrase bonuses ablated in
# abl1 (zero effect) were deleted afterwards, so there is no variant for them any more.
VARIANTS = {
    "base": {},
    "no_generic_penalty": {"GENERIC_RERANK_PENALTY": 0.0},
    "no_coverage": {"COVERAGE_BONUS": 0.0},
    "no_qa_docno": {"QA_BONUS": 0.0, "DOC_NO_BONUS": 0.0},
    "no_fusion_bonus": {"COVERAGE_BONUS": 0.0, "QA_BONUS": 0.0, "DOC_NO_BONUS": 0.0},
    "pure": {"GENERIC_RERANK_PENALTY": 0.0, "COVERAGE_BONUS": 0.0, "QA_BONUS": 0.0, "DOC_NO_BONUS": 0.0},
    "cand8": {"CANDIDATE_LIMIT": 8, "RERANK_SHORTLIST": 8},
    "cand12": {"CANDIDATE_LIMIT": 12, "RERANK_SHORTLIST": 12},
    "cand16": {"CANDIDATE_LIMIT": 16, "RERANK_SHORTLIST": 16},
    "cand32": {"CANDIDATE_LIMIT": 32, "RERANK_SHORTLIST": 32},
    "perdoc4": {"MAX_CHUNKS_PER_DOC": 4},
    "perdoc8": {"MAX_CHUNKS_PER_DOC": 8},
}


class CountingReranker:
    """Counts documents actually sent to the API (cache misses) and per-call sizes."""

    def __init__(self, inner):
        self.inner = inner
        self.sent = 0
        self.sizes = []

    def rerank(self, query, documents):
        self.sizes.append(len(documents))
        self.sent += sum(1 for d in documents if (query, d) not in self.inner.cache)
        return self.inner.rerank(query, documents)


def apply(variant):
    saved = {}
    for key, value in VARIANTS[variant].items():
        saved[key] = getattr(search_core, key)
        setattr(search_core, key, value)
    return saved


def restore(saved):
    for key, value in saved.items():
        setattr(search_core, key, value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--tag", default="abl1")
    args = parser.parse_args()

    search_core.ANSWERABILITY_OVERRIDE = False
    search_core.RERANK_MIN_SCORE = 0.75
    chunks = load_chunks()
    matrix = np.load(AB_DIR / "bge-small-zh.npy")
    ids = json.loads((AB_DIR / "bge-small-zh.ids.json").read_text(encoding="utf-8"))
    if ids != [c["chunk_id"] for c in chunks]:
        raise SystemExit("cached ids differ from chunks.jsonl")
    kb = OfflineKB(load_backend("bge-small-zh"), matrix)
    kb.load()
    reranker = CountingReranker(ab_embed.RERANKERS["bl-qwen3-rerank"]())
    kb.active_reranker = reranker
    sets = load_cases()

    out = OUT_DIR / f"results_{args.tag}.json"
    report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    for variant in args.variants.split(","):
        reranker.sent, reranker.sizes = 0, []
        saved = apply(variant)
        try:
            rows = evaluate(kb, sets, "always")
        finally:
            restore(saved)
        summary = summarize(rows, [])
        summary["rerank_docs_mean"] = round(float(np.mean(reranker.sizes)), 1)
        summary["api_docs_sent"] = reranker.sent
        report[variant] = {"summary": summary, "rows": rows}
        print(variant, json.dumps(summary, ensure_ascii=False), flush=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
