#!/usr/bin/env python3
"""P1 one-off: prepend ingest.DOC_SEARCH_ALIASES to matching chunks and re-embed them.

Run with --apply to write; default is a dry run.
"""
import argparse
import json
import os
import shutil

from common import COLLECTION, DATA_DIR, qdrant_client
from ingest import DOC_SEARCH_ALIASES, chunk_point, embed_texts, load_embedder

STAMP = "20261028-p1-alias"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    chunks_path = DATA_DIR / "chunks.jsonl"
    rows = [json.loads(line) for line in chunks_path.open(encoding="utf-8") if line.strip()]
    changed = []
    for title_key, alias in DOC_SEARCH_ALIASES:
        targets = [c for c in rows if title_key in c["doc_title"]]
        pending = [c for c in targets if not c["embed_text"].startswith(alias)]
        print(f"{title_key}: targets {len(targets)} changed {len(pending)}")
        for chunk in pending:
            chunk["embed_text"] = alias + "\n" + chunk["embed_text"]
        changed.extend(pending)
    if not changed:
        raise SystemExit("nothing to change")

    print(f"collection={COLLECTION} data={DATA_DIR}")
    if not args.apply:
        print("dry run; pass --apply to write")
        return

    backup = chunks_path.with_suffix(".jsonl.bak-" + STAMP)
    if not backup.exists():
        shutil.copy2(chunks_path, backup)
    tmp = chunks_path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for chunk in rows:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    os.replace(tmp, chunks_path)

    vectors = embed_texts(load_embedder(), [c["embed_text"] for c in changed], batch_size=8)
    points = [chunk_point(chunk, vector) for chunk, vector in zip(changed, vectors)]
    qdrant_client(timeout=60).upsert(collection_name=COLLECTION, points=points, wait=True)
    print("upserted", len(points), "vectors")


if __name__ == "__main__":
    main()
