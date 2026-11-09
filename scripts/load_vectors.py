#!/usr/bin/env python3
"""Build/refresh the Qdrant collection from precomputed Batch vectors.

Usage (repo root):
  KB_COLLECTION=cuc_kb_v3 python -m scripts.load_vectors \
      --vectors data/batch-qwen37/vectors.npz [--data-dir data-v3] [--recreate]

Reads chunks.jsonl from the data dir, matches every chunk to its vector by
chunk_id, then upserts. Refuses to run on partial mismatch so a half-built
collection can never go live.
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

from kb.common import COLLECTION, DATA_DIR, qdrant_client
from kb.ingest import UPSERT_BATCH, chunk_point, ensure_collection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", required=True, help="vectors.npz from batch_embed fetch")
    parser.add_argument("--data-dir", default="", help="defaults to KB_DATA_DIR / data")
    parser.add_argument("--recreate", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else DATA_DIR
    chunks = []
    with (data_dir / "chunks.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    archive = np.load(args.vectors, allow_pickle=False)
    ids = [str(x) for x in archive["ids"]]
    matrix = archive["vectors"]
    if matrix.shape[0] != len(ids):
        raise SystemExit(f"npz corrupt: {matrix.shape[0]} vectors vs {len(ids)} ids")
    row_of = {cid: i for i, cid in enumerate(ids)}
    if len(row_of) != len(ids):
        raise SystemExit("npz has duplicate ids")
    missing = [c["chunk_id"] for c in chunks if c["chunk_id"] not in row_of]
    if missing:
        raise SystemExit(f"{len(missing)} chunks have no vector, e.g. {missing[:3]}")
    extra = len(ids) - len({c["chunk_id"] for c in chunks})
    if extra:
        print(f"warning: {extra} vectors have no chunk in {data_dir} (ignored)", flush=True)

    client = qdrant_client(timeout=120)
    ensure_collection(client, int(matrix.shape[1]), args.recreate)
    started = time.time()
    for start in range(0, len(chunks), UPSERT_BATCH):
        batch = chunks[start : start + UPSERT_BATCH]
        points = [
            chunk_point(chunk, matrix[row_of[chunk["chunk_id"]]])
            for chunk in batch
        ]
        client.upsert(collection_name=COLLECTION, points=points, wait=True)
        print(f"upserted {min(start + UPSERT_BATCH, len(chunks))}/{len(chunks)}", flush=True)
    count = client.count(COLLECTION, exact=True).count
    print(
        f"collection {COLLECTION}: {count} points, dim={matrix.shape[1]}, "
        f"{time.time() - started:.1f}s",
        flush=True,
    )
    if count != len(chunks):
        raise SystemExit(f"point count {count} != chunk count {len(chunks)}")


if __name__ == "__main__":
    main()