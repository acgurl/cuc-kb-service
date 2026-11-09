#!/usr/bin/env python3
"""Sync provenance payload fields (source_url/publish_date/date_basis/provenance)
to every point, grouped by doc_id.

Reads a provenance.json mapping doc_id -> {source_url, publish_date, date_basis,
provenance}. Scrolls point ids once (doc_id is not an indexed field, so per-doc
filters would full-scan 2290 times), then sets payload by explicit point ids.

Usage (load .env first: set -a; . ./.env; set +a):
  python -m scripts.sync_provenance --manifest batch-provenance-20261109/provenance.json --apply
  python -m scripts.sync_provenance --manifest ... --verify
  python -m scripts.sync_provenance --reverse   # delete the four fields
"""
import argparse
import collections
import json
from pathlib import Path

from kb.common import COLLECTION, qdrant_client

FIELDS = ("source_url", "publish_date", "date_basis", "provenance")
QA = {"source_url": "", "publish_date": "", "date_basis": "无", "provenance": "QA模板"}


def scroll_doc_ids(client):
    groups = collections.defaultdict(list)
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION,
            limit=1000,
            offset=offset,
            with_payload=["doc_id"],
            with_vectors=False,
        )
        for p in points:
            groups[(p.payload or {}).get("doc_id", "")].append(p.id)
        if offset is None:
            break
    return groups


def apply(client, manifest):
    prov = json.loads(Path(manifest).read_text(encoding="utf-8"))
    groups = scroll_doc_ids(client)
    unknown = set(groups) - set(prov) - {"qa-csv"}
    if unknown:
        raise SystemExit(f"collection 含 manifest 之外的 doc_id：{sorted(unknown)[:5]}")
    total = 0
    for i, (doc_id, ids) in enumerate(sorted(groups.items()), 1):
        rec = QA if doc_id == "qa-csv" else prov[doc_id]
        payload = {k: rec.get(k, "") for k in FIELDS}
        client.set_payload(collection_name=COLLECTION, payload=payload, points=ids, wait=True)
        total += len(ids)
        if i % 200 == 0:
            print(f"  {i}/{len(groups)} docs, {total} points", flush=True)
    print(f"provenance payload synced: {len(groups)} docs / {total} points")


def verify(client, manifest):
    prov = json.loads(Path(manifest).read_text(encoding="utf-8"))
    offset = None
    checked = bad = 0
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION, limit=1000, offset=offset,
            with_payload=list(FIELDS) + ["doc_id"], with_vectors=False,
        )
        for p in points:
            payload = p.payload or {}
            doc_id = payload.get("doc_id", "")
            rec = QA if doc_id == "qa-csv" else prov.get(doc_id, {})
            for k in FIELDS:
                checked += 1
                if payload.get(k, "") != rec.get(k, ""):
                    bad += 1
                    if bad <= 5:
                        print(f"MISMATCH {p.id} {doc_id} {k}: "
                              f"{payload.get(k)!r} != {rec.get(k)!r}")
        if offset is None:
            break
    print(f"verify: {checked} field values checked, {bad} mismatches")
    if bad:
        raise SystemExit(1)


def reverse(client):
    groups = scroll_doc_ids(client)
    all_ids = [pid for ids in groups.values() for pid in ids]
    client.delete_payload(collection_name=COLLECTION, keys=list(FIELDS), points=all_ids, wait=True)
    print(f"provenance payload removed from {len(all_ids)} points")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="batch-provenance-20261109/provenance.json")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--reverse", action="store_true")
    args = parser.parse_args()
    client = qdrant_client(timeout=120)
    if args.reverse:
        reverse(client)
    elif args.apply:
        apply(client, args.manifest)
    elif args.verify:
        verify(client, args.manifest)
    else:
        parser.error("one of --apply / --verify / --reverse is required")


if __name__ == "__main__":
    main()