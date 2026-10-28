#!/usr/bin/env python3
import json
import sys

from common import (
    RERANK_API_BASE,
    RERANK_API_HEADER,
    RERANK_API_KEY,
    RERANK_API_MODEL,
    RERANK_API_PATH,
    RERANK_API_TIMEOUT,
    RERANK_MIN_SCORE,
    RERANK_PROVIDER,
)
from search_core import CloudReranker

QUERY = "宿舍床尺寸是多少"
DOCUMENTS = [
    "宿舍条件怎么样？房间内的双层铁床以2000*900*2200 的为主，衣柜以500*600*2000 的为主。",
    "第二十三条 票据领用部门或个人应妥善保管收费票据，如发生票据丢失应书面报告财务处。",
    "今天天气不错，适合出门散步。",
]


def main():
    print(f"provider={RERANK_PROVIDER} model={RERANK_API_MODEL or '(empty)'} threshold={RERANK_MIN_SCORE}")
    if RERANK_PROVIDER not in ("openai", "cloud", "openai-compatible"):
        print("KB_RERANK_PROVIDER is not openai/cloud")
        return 2
    if not RERANK_API_BASE or not RERANK_API_MODEL:
        print("missing RERANK_API_BASE or RERANK_API_MODEL")
        return 2

    reranker = CloudReranker(
        base=RERANK_API_BASE,
        path=RERANK_API_PATH,
        model=RERANK_API_MODEL,
        api_key=RERANK_API_KEY,
        header=RERANK_API_HEADER,
        timeout=RERANK_API_TIMEOUT,
    )
    print(f"url={reranker.url} key={'set' if RERANK_API_KEY else 'empty'}")
    try:
        scores = reranker.rerank(QUERY, DOCUMENTS)
    except Exception as exc:
        print(f"failed: {exc}")
        return 1

    print(json.dumps({"query": QUERY, "scores": scores}, ensure_ascii=False, indent=2))
    print("ranking:")
    for index in sorted(range(len(scores)), key=lambda i: scores[i] or -1e9, reverse=True):
        print(f"  {scores[index]:.4f}  {DOCUMENTS[index][:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
