#!/usr/bin/env python3
"""data-v8 汇编版本收敛 payload 同步（Phoenix 上跑，向量不变）。

用法（先 set -a; . ./.env; set +a）：
  .venv/bin/python -m scripts.sync_v8_family --verify
  .venv/bin/python -m scripts.sync_v8_family
  .venv/bin/python -m scripts.sync_v8_family --reverse

manifest 由 scripts/apply_v8_family.py 生成（版本收敛-20261110/manifest.json）。
按 doc_id 一次 set_payload，与 sync_assistants_repartition / bulk_dept_sync 同模式。
"""
import argparse
import json
from pathlib import Path

from qdrant_client.models import FieldCondition, Filter, MatchAny

from kb.common import COLLECTION, qdrant_client

DEFAULT_MANIFEST = Path("batch-version-family-20261110/manifest.json")


def _values(manifest, key):
    """doc_id -> payload for forward (changes) or reverse (before)."""
    return {doc_id: data for doc_id, data in manifest[key].items() if data}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--reverse", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    client = qdrant_client(timeout=180)

    if args.verify:
        for label, key in (("current", "changes"), ("baseline", "before")):
            print(f"[{label}]")
            for doc_id, payload in _values(manifest, key).items():
                flt = Filter(must=[FieldCondition(key="doc_id", match=MatchAny(any=[doc_id]))])
                n = client.count(collection_name=COLLECTION, count_filter=flt, exact=True).count
                print(f"  {doc_id}: {n} chunks -> {json.dumps(payload, ensure_ascii=False)}")
        return

    payloads = _values(manifest, "before" if args.reverse else "changes")
    for doc_id, payload in payloads.items():
        flt = Filter(must=[FieldCondition(key="doc_id", match=MatchAny(any=[doc_id]))])
        before = client.count(collection_name=COLLECTION, count_filter=flt, exact=True).count
        client.set_payload(
            collection_name=COLLECTION, payload=payload, points=flt, wait=True
        )
        print(f"  {doc_id}: {before} chunks <- {json.dumps(payload, ensure_ascii=False)}")
    print(f"payload synced: {len(payloads)} docs ({'reverse' if args.reverse else 'forward'})")


if __name__ == "__main__":
    main()
