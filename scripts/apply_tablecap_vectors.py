#!/usr/bin/env python3
"""Apply enriched table vectors to a live collection, saving originals for rollback.

Retrieves the target points (by uuid5(chunk_id)) with their current vectors and
payload, writes the original vectors to --rollback-out (npz), then upserts the
enriched vectors from --vectors with the *unchanged* payload. Only the listed
points are touched.

  python -m scripts.apply_tablecap_vectors --vectors data-tablecap/batch/vectors.npz \
      --collection cuc_kb_v4 --rollback-out data/tablecap_orig_vectors.npz          # dry-run
  ... --apply                                                                       # upsert
Rollback: re-upsert --rollback-out vectors the same way.
"""

import argparse
import uuid

import numpy as np
from qdrant_client.models import PointStruct

from kb.common import qdrant_client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", required=True)
    parser.add_argument("--collection", default="cuc_kb_v4")
    parser.add_argument("--rollback-out", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    archive = np.load(args.vectors, allow_pickle=False)
    ids = [str(x) for x in archive["ids"]]
    matrix = archive["vectors"]
    enriched = {
        str(uuid.uuid5(uuid.NAMESPACE_URL, cid)): (cid, matrix[i]) for i, cid in enumerate(ids)
    }
    client = qdrant_client(timeout=120)
    points = client.retrieve(args.collection, ids=list(enriched.keys()), with_vectors=True, with_payload=True)
    if len(points) != len(enriched):
        raise SystemExit(f"retrieved {len(points)} != {len(enriched)} target points")

    orig_ids, orig_vecs, payloads = [], [], {}
    for p in points:
        pid = str(p.id)
        cid, _ = enriched[pid]
        vec = p.vector["dense"] if isinstance(p.vector, dict) else p.vector
        orig_ids.append(cid)
        orig_vecs.append(vec)
        payloads[pid] = p.payload
    np.savez(args.rollback_out, ids=np.asarray(orig_ids), vectors=np.asarray(orig_vecs, dtype=np.float32))
    print(f"saved {len(orig_ids)} original vectors -> {args.rollback_out}", flush=True)

    if not args.apply:
        print("dry-run; pass --apply to upsert enriched vectors", flush=True)
        return
    batch = [
        PointStruct(id=pid, vector={"dense": [float(x) for x in vec]}, payload=payloads[pid])
        for pid, (_, vec) in enriched.items()
    ]
    client.upsert(args.collection, points=batch, wait=True)
    print(f"upserted {len(batch)} enriched vectors into {args.collection}", flush=True)


if __name__ == "__main__":
    main()