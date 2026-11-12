#!/usr/bin/env python3
"""Patch publish_date/date_basis in a local data dir from a small manifest.

Manifest: {doc_id: {"old": "...", "new": "...", "old_basis": "...", "new_basis": "..."}}
Usage:
  python -m scripts.apply_dates_patch --data-dir data-v9 --manifest dates_extra.json
  python -m scripts.apply_dates_patch --data-dir data --manifest ... --reverse
"""
import argparse
import json
import os
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--reverse", action="store_true")
    args = ap.parse_args()

    data = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    base = Path(args.data_dir)
    docs_path = base / "docs.json"
    chunks_path = base / "chunks.jsonl"

    def target(rec):
        if args.reverse:
            return rec.get("old", ""), rec.get("old_basis", "")
        return rec.get("new", ""), rec.get("new_basis", "")

    docs = json.loads(docs_path.read_text(encoding="utf-8"))
    seen = set()
    for d in docs:
        rec = data.get(d["doc_id"])
        if not rec:
            continue
        d["publish_date"], d["date_basis"] = target(rec)
        seen.add(d["doc_id"])

    tmp = str(docs_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=1)
    os.replace(tmp, docs_path)

    n = touched = 0
    tmpc = str(chunks_path) + ".tmp"
    with open(chunks_path, encoding="utf-8") as f, open(tmpc, "w", encoding="utf-8") as g:
        for line in f:
            c = json.loads(line)
            rec = data.get(c["doc_id"])
            if rec:
                c["publish_date"], c["date_basis"] = target(rec)
                touched += 1
            g.write(json.dumps(c, ensure_ascii=False) + "\n")
            n += 1
    os.replace(tmpc, chunks_path)

    missing = set(data) - seen
    print(f"{'reverse' if args.reverse else 'apply'}: {len(seen)} docs / {touched} chunks; total chunks {n}")
    if missing:
        print("WARN missing docs:", sorted(missing))


if __name__ == "__main__":
    main()
