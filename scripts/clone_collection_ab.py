#!/usr/bin/env python3
"""Clone a Qdrant collection into a temp target, overriding selected vectors.

Non-destructive A/B helper: scrolls --source (with vectors) and upserts into
--target, substituting the enriched vectors from --vectors (npz keyed by
chunk_id) for the matching points. The live collection is never modified.

  KB_COLLECTION unused; pass --source/--target explicitly.
  python -m scripts.clone_collection_ab --source cuc_kb_v4 \
      --target cuc_kb_v4_tablecap --vectors data-tablecap/batch/vectors.npz
"""

import argparse
import uuid

import numpy as np
from qdrant_client.models import Distance, PayloadSchemaType, PointStruct, VectorParams

from kb.common import qdrant_client

PAYLOAD_FIELDS = ["chunk_id", "status", "assistants", "doc_type", "doc_no"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="cuc_kb_v4")
    parser.add_argument("--target", default="cuc_kb_v4_tablecap")
    parser.add_argument("--vectors", required=True)
    parser.add_argument("--dim", type=int, default=1024)
    args = parser.parse_args()

    archive = np.load(args.vectors, allow_pickle=False)
    ids = [str(x) for x in archive["ids"]]
    matrix = archive["vectors"]
    enriched = {
        str(uuid.uuid5(uuid.NAMESPACE_URL, cid)): matrix[i] for i, cid in enumerate(ids)
    }
    print(f"enriched vectors to override: {len(enriched)}", flush=True)

    client = qdrant_client(timeout=120)
    if client.collection_exists(args.target):
        client.delete_collection(args.target)
    client.create_collection(
        args.target,
        vectors_config={"dense": VectorParams(size=args.dim, distance=Distance.COSINE)},
    )
    for field in ("assistants", "doc_type", "status", "doc_no"):
        try:
            client.create_payload_index(args.target, field_name=field, field_schema=PayloadSchemaType.KEYWORD)
        except Exception:
            pass

    offset = None
    total = overridden = 0
    while True:
        points, offset = client.scroll(
            args.source, limit=256, offset=offset,
            with_vectors=True, with_payload=PAYLOAD_FIELDS,
        )
        if not points:
            break
        batch = []
        for p in points:
            vector = p.vector
            if str(p.id) in enriched:
                vector = {"dense": [float(x) for x in enriched[str(p.id)]]}
                overridden += 1
            batch.append(PointStruct(id=p.id, vector=vector, payload=p.payload))
        client.upsert(args.target, points=batch, wait=True)
        total += len(batch)
        if total % 5120 == 0:
            print(f"cloned {total} ...", flush=True)
        if offset is None:
            break
    count = client.count(args.target, exact=True).count
    src_count = client.count(args.source, exact=True).count
    print(f"cloned {total} points, overridden {overridden}; target={count} source={src_count}", flush=True)
    if count != src_count or overridden != len(enriched):
        raise SystemExit("clone mismatch — do not use for A/B")


if __name__ == "__main__":
    main()