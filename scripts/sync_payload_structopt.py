#!/usr/bin/env python3
"""Sync Qdrant payload for the 2026-11-08 structure optimization.

Vectors are unchanged; only payload metadata is updated to match data-v5:
  * superseded docs  -> payload status=superseded (+status_reason)
  * department fixes -> payload department=<source unit>

Dense retrieval filters on payload status=active, so this step is required
for the marks to take effect (chunks.jsonl alone only covers the BM25 path).

  python -m scripts.sync_payload_structopt --manifest <manifest.json> [--apply]

Default is a dry run. Run from repo root on Phoenix after swapping data.
"""
import argparse
import json
from pathlib import Path

from qdrant_client.models import FieldCondition, Filter, MatchValue

from kb.common import COLLECTION, qdrant_client


def point_ids(client, key, value):
    points = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=Filter(must=[FieldCondition(key=key, match=MatchValue(value=value))]),
            limit=512,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        points.extend(p.id for p in batch)
        if offset is None:
            return points


def set_by_doc(client, doc_id, payload):
    pts = point_ids(client, "doc_id", doc_id)
    if pts:
        client.set_payload(collection_name=COLLECTION, payload=payload, points=pts, wait=True)
    return len(pts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    m = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    sup = m["superseded"]
    dept = m["department_changes"]
    print(f"collection={COLLECTION} superseded_docs={len(sup)} department_docs={len(dept)}")
    if not args.apply:
        print("dry run; pass --apply to write Qdrant payload")
        return
    client = qdrant_client(timeout=120)
    sup_points = 0
    for doc_id, reason in sup.items():
        if reason == "active":
            payload = {"status": "active", "status_reason": ""}
        else:
            payload = {"status": "superseded", "status_reason": reason}
        n = set_by_doc(client, doc_id, payload)
        sup_points += n
    print(f"status: {len(sup)} docs / {sup_points} points set")
    dept_points = 0
    for doc_id, new_dept in dept.items():
        n = set_by_doc(client, doc_id, {"department": new_dept})
        dept_points += n
    print(f"department: {len(dept)} docs / {dept_points} points set")
    # verify a sample
    sample = next(iter(sup))
    pts = point_ids(client, "doc_id", sample)
    print(f"verify sample {sample}: {len(pts)} points")
    print("applied")


if __name__ == "__main__":
    main()