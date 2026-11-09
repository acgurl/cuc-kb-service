#!/usr/bin/env python3
"""One-off: merge the live KB data with the 20261106 official-site batch.

Applies kb_flags.json (live docs replaced by fuller official versions are
soft-marked superseded, same convention as SUPERSEDED_DOC_IDS), checks id
collisions, and writes a combined data dir ready for batch embedding.

  python -m scripts.merge_data_v3 \
      --live data --new <batch>/data-official --flags <batch>/kb_flags.json \
      --out data-v3
"""

import argparse
import json
from collections import Counter
from pathlib import Path


def read_chunks(path):
    chunks = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--flags", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    live_dir, new_dir, out_dir = Path(args.live), Path(args.new), Path(args.out)
    flags = json.loads(Path(args.flags).read_text(encoding="utf-8"))

    live_chunks = read_chunks(live_dir / "chunks.jsonl")
    new_chunks = read_chunks(new_dir / "chunks.jsonl")
    live_docs = json.loads((live_dir / "docs.json").read_text(encoding="utf-8"))
    new_docs = json.loads((new_dir / "docs.json").read_text(encoding="utf-8"))

    live_ids = {c["chunk_id"] for c in live_chunks}
    collide = live_ids & {c["chunk_id"] for c in new_chunks}
    if collide:
        raise SystemExit(f"chunk_id collisions: {list(collide)[:5]}")
    live_doc_ids = {d["doc_id"] for d in live_docs}
    doc_collide = live_doc_ids & {d["doc_id"] for d in new_docs}
    if doc_collide:
        raise SystemExit(f"doc_id collisions: {list(doc_collide)[:5]}")
    missing_flags = set(flags) - live_doc_ids
    if missing_flags:
        raise SystemExit(f"flag targets not in live data: {missing_flags}")

    flagged_chunks = 0
    for chunk in live_chunks:
        if chunk["doc_id"] in flags:
            chunk["status"] = "superseded"
            flagged_chunks += 1
    for doc in live_docs:
        if doc["doc_id"] in flags:
            doc["status"] = "superseded"

    merged_chunks = live_chunks + new_chunks
    merged_docs = live_docs + new_docs
    status_counts = Counter(c["status"] for c in merged_chunks)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in merged_chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    (out_dir / "docs.json").write_text(
        json.dumps(merged_docs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"merged: {len(merged_docs)} docs / {len(merged_chunks)} chunks "
        f"(live {len(live_docs)}/{len(live_chunks)} + new {len(new_docs)}/{len(new_chunks)})"
    )
    print(f"flags applied: {len(flags)} docs, {flagged_chunks} chunks -> superseded")
    print(f"chunk status: {dict(status_counts)}")


if __name__ == "__main__":
    main()