#!/usr/bin/env python3
import json
import sys
from pathlib import Path


def main():
    dataset = json.loads(Path("eval/dataset_v2.json").read_text(encoding="utf-8"))
    chunks = [
        json.loads(line)
        for line in Path("data/chunks.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    active = [c for c in chunks if c.get("status") == "active"]
    print("active chunks:", len(active))

    problems = 0
    for category in ("curated", "short"):
        print(f"--- {category} ---")
        for case in dataset.get(category, []):
            if case.get("expect_no_answer"):
                continue
            docs = [
                c
                for c in active
                if any(token in c["doc_title"] for token in case["expected_docs"])
            ]
            if not docs:
                print("DOC MISSING", case["id"], case["query"], case["expected_docs"])
                problems += 1
                continue
            haystack = "\n".join(
                c["text"] + " " + " ".join(c.get("section_path") or []) for c in docs
            )
            if not any(keyword in haystack for keyword in case["keywords"]):
                print(
                    "KEYWORD MISSING",
                    case["id"],
                    case["query"],
                    case["keywords"],
                    "| docs:",
                    sorted({c["doc_title"][:40] for c in docs})[:3],
                )
                problems += 1

    print("--- isolation doc titles ---")
    all_titles = {c["doc_title"] for c in active}
    for case in dataset.get("isolation", []):
        for title in case.get("expected_docs", []) + case.get("forbidden_docs", []):
            if not any(title in actual for actual in all_titles):
                print("ISOLATION DOC MISSING", case["id"], title)
                problems += 1

    print("problems:", problems)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
