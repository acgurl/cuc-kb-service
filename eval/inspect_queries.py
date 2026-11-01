#!/usr/bin/env python3
"""Print ranked results for a list of debug queries.

A query set is a JSON list of [case_id, query, assistant_or_null]; see inspect_sets/.

  python eval/inspect_queries.py eval/inspect_sets/p0.json
  python eval/inspect_queries.py eval/inspect_sets/top50.json --top-k 20
  python eval/inspect_queries.py eval/inspect_sets/answerability.json --compact
"""
import argparse
import json
import os
from pathlib import Path

from kbeval import DEFAULT_API, search, top_title


def print_full(case_id, query, assistant, response, show_text):
    print(
        "\n=====", case_id, query, "| assistant=", assistant,
        "| no_answer=", response["no_answer"], "conf=", response["confidence"],
    )
    for rank, item in enumerate(response["results"], 1):
        citation = item["citation"]
        line = "  %d. [%s] p%s-%s dense=%s rerank=%s generic=%s" % (
            rank, citation["doc_title"][:58], citation.get("page_start"),
            citation.get("page_end"), item.get("dense_score"),
            item.get("rerank_score"), item.get("is_generic"),
        )
        if show_text:
            line += " :: " + " ".join(item["text"].split())[:170]
        print(line)


def print_compact(case_id, response):
    title = top_title(response["results"])
    print("%-4s no_answer=%-5s conf=%-6s ans=%-40s top=%s" % (
        case_id, response["no_answer"], response["confidence"],
        json.dumps(response.get("answerability"), ensure_ascii=False),
        title[:52] if title else None,
    ))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("queries", help="JSON file of [case_id, query, assistant]")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--token", default=os.getenv("KB_API_TOKEN", ""))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--compact", action="store_true", help="one line per query")
    parser.add_argument("--text", action="store_true", help="include chunk text")
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("KB_API_TOKEN is required")
    for case_id, query, assistant in json.loads(Path(args.queries).read_text(encoding="utf-8")):
        response = search(args.api, args.token, query, assistant, args.top_k, timeout=120)
        if args.compact:
            print_compact(case_id, response)
        else:
            print_full(case_id, query, assistant, response, args.text)


if __name__ == "__main__":
    main()
