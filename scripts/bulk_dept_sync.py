#!/usr/bin/env python3
"""Fast bulk department payload sync: group doc_ids by target department,
one set_payload(Filter doc_id MatchAny) per group. Idempotent."""
import json, collections
from pathlib import Path
from qdrant_client.models import FieldCondition, Filter, MatchAny
from kb.common import COLLECTION, qdrant_client

m = json.loads(Path("batch-structopt-20261108/manifest.json").read_text(encoding="utf-8"))
groups = collections.defaultdict(list)
for doc_id, dept in m["department_changes"].items():
    groups[dept].append(doc_id)
c = qdrant_client(timeout=120)
total = 0
for dept, ids in sorted(groups.items(), key=lambda kv: -len(kv[1])):
    flt = Filter(must=[FieldCondition(key="doc_id", match=MatchAny(any=ids))])
    c.set_payload(collection_name=COLLECTION, payload={"department": dept}, points=flt, wait=True)
    total += len(ids)
    print(f"  {dept}: {len(ids)} docs")
print(f"department payload synced: {total} docs in {len(groups)} groups")