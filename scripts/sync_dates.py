#!/usr/bin/env python3
"""Apply date-only payload fixes (publish_date/date_basis) for changed docs.

Manifest: dates_fix.json from 日期复核-20261110
  {doc_id: {"old": "...", "new": "...", "old_basis": "...", "new_basis": "...", ...}}

Usage (load .env first):
  python -m scripts.sync_dates --manifest batch-dates-20261110/dates_fix.json --apply
  python -m scripts.sync_dates --manifest ... --verify
  python -m scripts.sync_dates --manifest ... --reverse   # restore old dates
"""
import argparse
import collections
import json
from pathlib import Path

from kb.common import COLLECTION, qdrant_client

FIELDS = ("publish_date", "date_basis")


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


def target(rec, reverse):
    if reverse:
        return {"publish_date": rec.get("old", ""), "date_basis": rec.get("old_basis", "")}
    return {"publish_date": rec.get("new", ""), "date_basis": rec.get("new_basis", "")}


def apply(client, manifest, reverse):
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    groups = scroll_doc_ids(client)
    missing = [d for d in data if d not in groups]
    total = 0
    for i, (doc_id, rec) in enumerate(sorted(data.items()), 1):
        ids = groups.get(doc_id)
        if not ids:
            continue
        client.set_payload(
            collection_name=COLLECTION, payload=target(rec, reverse), points=ids, wait=True
        )
        total += len(ids)
        if i % 50 == 0:
            print(f"  {i}/{len(data)} docs, {total} points", flush=True)
    print(f"{'reverse' if reverse else 'apply'}: {len(data) - len(missing)} docs / {total} points")
    if missing:
        print(f"WARN {len(missing)} doc_ids not found in collection: {missing[:5]}")


def verify(client, manifest, reverse):
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    groups = scroll_doc_ids(client)
    checked = bad = 0
    for doc_id, rec in data.items():
        ids = groups.get(doc_id)
        if not ids:
            continue
        points = client.retrieve(
            collection_name=COLLECTION, ids=ids, with_payload=list(FIELDS), with_vectors=False
        )
        exp = target(rec, reverse)
        for p in points:
            for k in FIELDS:
                checked += 1
                got = (p.payload or {}).get(k, "")
                if got != exp[k]:
                    bad += 1
                    if bad <= 5:
                        print(f"MISMATCH {p.id} {doc_id} {k}: {got!r} != {exp[k]!r}")
    print(f"verify: {checked} field values checked, {bad} mismatches")
    if bad:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--reverse", action="store_true")
    args = parser.parse_args()
    client = qdrant_client(timeout=120)
    if args.apply:
        apply(client, args.manifest, reverse=args.reverse)
    elif args.verify:
        verify(client, args.manifest, reverse=args.reverse)
    else:
        parser.error("one of --apply / --verify is required")


if __name__ == "__main__":
    main()
