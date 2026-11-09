#!/usr/bin/env python3
"""One-off: apply the 2026-11-08 old-doc update to the merged v3 data.

The live merged set (data-v3 = 311 live + 1936 official) cannot be re-merged
with merge_data_v3 (doc_id collisions). Instead this takes data-v3 as the
base, replaces the official portion with the updated data-official
(1979 docs: +43 new, 100 old marked superseded), keeps the 311 live docs
untouched, and writes:

  --out           merged data dir (docs.json + chunks.jsonl) for deployment
  --new-chunks    chunks of the 43 new docs only, for incremental batch embed

Verifies that shared official chunks are identical apart from `status`, so
existing vectors can be reused without re-embedding.

  python -m scripts.merge_update_20261108 \
      --base data-v3 --official <batch>/data-official \
      --out data-v4 --new-chunks batch-qwen37-20261108/chunks_new.jsonl
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
    parser.add_argument("--base", required=True, help="live merged data dir (data-v3)")
    parser.add_argument("--official", required=True, help="updated data-official dir")
    parser.add_argument("--out", required=True)
    parser.add_argument("--new-chunks", required=True)
    args = parser.parse_args()

    base_dir, official_dir = Path(args.base), Path(args.official)
    base_docs = json.loads((base_dir / "docs.json").read_text(encoding="utf-8"))
    base_chunks = read_chunks(base_dir / "chunks.jsonl")
    new_docs = json.loads((official_dir / "docs.json").read_text(encoding="utf-8"))
    new_chunks = read_chunks(official_dir / "chunks.jsonl")

    base_doc_ids = {d["doc_id"] for d in base_docs}
    new_doc_ids = {d["doc_id"] for d in new_docs}
    live_only_ids = base_doc_ids - new_doc_ids
    added_ids = new_doc_ids - base_doc_ids
    shared_ids = base_doc_ids & new_doc_ids
    print(
        f"base {len(base_docs)} docs: live-only {len(live_only_ids)}, "
        f"shared official {len(shared_ids)}, added {len(added_ids)}"
    )

    # Shared chunks must be text-identical (only status may differ), so the
    # existing vectors stay valid.
    base_chunk_of = {c["chunk_id"]: c for c in base_chunks}
    drift = []
    for chunk in new_chunks:
        if chunk["doc_id"] not in shared_ids:
            continue
        old = base_chunk_of.get(chunk["chunk_id"])
        if old is None:
            drift.append((chunk["chunk_id"], "missing in base"))
            continue
        a = {k: v for k, v in old.items() if k != "status"}
        b = {k: v for k, v in chunk.items() if k != "status"}
        if a != b:
            drift.append((chunk["chunk_id"], "content differs"))
    if drift:
        raise SystemExit(f"shared-chunk drift, cannot reuse vectors: {drift[:5]}")
    base_shared_count = sum(1 for c in base_chunks if c["doc_id"] in shared_ids)
    new_shared_count = sum(1 for c in new_chunks if c["doc_id"] in shared_ids)
    if base_shared_count != new_shared_count:
        raise SystemExit(
            f"shared chunk count changed: {base_shared_count} -> {new_shared_count}"
        )

    # Keep every base chunk that is not part of the shared official portion.
    # This includes pseudo-doc chunks absent from docs.json (qa-csv, 52 QA
    # chunks ingested separately from the QA CSV).
    merged_docs = [d for d in base_docs if d["doc_id"] not in new_doc_ids] + new_docs
    merged_chunks = [
        c for c in base_chunks if c["doc_id"] not in new_doc_ids
    ] + new_chunks
    added_chunks = [c for c in new_chunks if c["doc_id"] in added_ids]

    ids = [c["chunk_id"] for c in merged_chunks]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate chunk_id in merged output")
    doc_ids = [d["doc_id"] for d in merged_docs]
    if len(doc_ids) != len(set(doc_ids)):
        raise SystemExit("duplicate doc_id in merged output")
    base_orphan_ids = {c["doc_id"] for c in base_chunks} - base_doc_ids
    orphan = [
        c["chunk_id"]
        for c in merged_chunks
        if c["doc_id"] not in set(doc_ids) and c["doc_id"] not in base_orphan_ids
    ]
    if orphan:
        raise SystemExit(f"orphan chunks: {orphan[:5]}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in merged_chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    (out_dir / "docs.json").write_text(
        json.dumps(merged_docs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    new_path = Path(args.new_chunks)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    with new_path.open("w", encoding="utf-8") as handle:
        for chunk in added_chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    print(f"merged: {len(merged_docs)} docs / {len(merged_chunks)} chunks")
    print(f"chunk status: {dict(Counter(c['status'] for c in merged_chunks))}")
    print(f"doc status: {dict(Counter(d.get('status', 'active') for d in merged_docs))}")
    print(f"new chunks for batch embed: {len(added_chunks)} -> {new_path}")


if __name__ == "__main__":
    main()