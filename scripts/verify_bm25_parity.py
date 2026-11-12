#!/usr/bin/env python3
"""Verify the sparse BM25 rewrite is lossless on the full corpus.

Compares, bit-for-bit, the postings-based scorer/filter against the original
rank_bm25 get_scores + per-chunk _matches loop, over every eval query and a
matrix of assistant/doc_type filters; then benchmarks both paths. No Qdrant,
embedding or rerank access is needed.

  python -m scripts.verify_bm25_parity [--data-dir data-v7] [--bench-runs 3]
"""

import argparse
import json
import time
from pathlib import Path

import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from kb.search_core import KB


def builtin_queries():
    return [
        "奖学金申请条件是什么", "学生宿舍床位尺寸", "差旅住宿费报销标准",
        "研究生学位论文答辩流程", "学费多少钱一年", "如何办理校园网",
        "休学需要哪些材料", "今天食堂几点开门", "中传财字〔2024〕229号 住宿",
        "住宿 住宿 报销", "完全不存在的词xyz", "学生", "的",
    ]


def eval_queries(root):
    queries = []
    for path in sorted((root / "eval").glob("dataset*.json")) + sorted(
        (root / "eval" / "heldout").glob("dataset_*.json")
    ):
        try:
            dataset = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for cases in dataset.values():
            if isinstance(cases, list):
                queries.extend(
                    case["query"] for case in cases
                    if isinstance(case, dict) and case.get("query")
                )
    return queries


def legacy_search(kb, query, limit, assistant, doc_types):
    scores = kb.bm25.get_scores(jieba.lcut_for_search(query))
    candidates = []
    for index, score in enumerate(scores):
        if score <= 0:
            continue
        chunk = kb.chunks[index]
        if kb._matches(chunk, assistant, doc_types):
            candidates.append((chunk["chunk_id"], float(score)))
    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates[:limit]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--bench-runs", type=int, default=3)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = root / data_dir

    chunks = []
    with (data_dir / "chunks.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    print(f"loaded {len(chunks)} chunks from {data_dir}", flush=True)

    kb = KB()
    for chunk in chunks:
        kb._add_chunk(chunk)
    started = time.time()
    tokenized = [jieba.lcut_for_search(chunk["embed_text"]) for chunk in chunks]
    kb.bm25 = BM25Okapi(tokenized)
    print(f"tokenize + BM25Okapi: {time.time() - started:.1f}s", flush=True)
    started = time.time()
    kb._build_bm25_index()
    print(f"sparse index + masks: {time.time() - started:.1f}s, "
          f"{len(kb.bm25_postings)} terms", flush=True)

    queries = list(dict.fromkeys(builtin_queries() + eval_queries(root)))
    assistants = sorted({
        assistant for chunk in chunks for assistant in (chunk.get("assistants") or [])
    })
    doc_types = sorted({chunk.get("doc_type") for chunk in chunks if chunk.get("doc_type")})
    filters = [(None, None), ("不存在的助手", None), (None, ["不存在的类型"])]
    filters += [(assistant, None) for assistant in assistants]
    filters += [(None, [doc_type]) for doc_type in doc_types]
    filters += [(None, doc_types[:3]), (assistants[0], doc_types[:2])]
    print(f"parity: {len(queries)} queries x {len(filters)} filters", flush=True)

    checked = 0
    matrix_queries = queries[:20]
    for query in queries:
        tokens = jieba.lcut_for_search(query)
        expected = kb.bm25.get_scores(tokens)
        actual = kb._bm25_scores(tokens)
        if not np.array_equal(actual, expected):
            diff = np.flatnonzero(actual != expected)
            raise SystemExit(f"score mismatch for {query!r} at {diff[:10].tolist()}")
        # Unfiltered candidate parity for every query; the full filter matrix
        # runs on a representative subset (scores are already proven equal).
        query_filters = filters if query in matrix_queries else [(None, None)]
        for assistant, filter_types in query_filters:
            got = kb.bm25_search(query, 40, assistant, filter_types)
            want = legacy_search(kb, query, 40, assistant, filter_types)
            if got != want:
                raise SystemExit(
                    f"candidate mismatch for {query!r} assistant={assistant} "
                    f"types={filter_types}\n got={got[:5]}\nwant={want[:5]}"
                )
            checked += 1
    print(f"parity OK: scores bit-identical, {checked} filter comparisons equal", flush=True)

    sample = queries[:20]
    for label, run in (
        ("legacy", lambda q: legacy_search(kb, q, 40, None, None)),
        ("sparse", lambda q: kb.bm25_search(q, 40, None, None)),
    ):
        run(sample[0])  # warm jieba
        started = time.time()
        for _ in range(args.bench_runs):
            for query in sample:
                run(query)
        elapsed = (time.time() - started) / (args.bench_runs * len(sample))
        print(f"{label}: {elapsed * 1000:.1f} ms/query (avg of {len(sample)} queries "
              f"x {args.bench_runs} runs)", flush=True)


if __name__ == "__main__":
    main()