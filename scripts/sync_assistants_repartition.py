#!/usr/bin/env python3
"""助手归属重划 payload 同步（Phoenix 上跑）。

用法（先 set -a; . ./.env; set +a）：
  .venv/bin/python -m scripts.sync_assistants_repartition            # 正向：追加领域标签
  .venv/bin/python -m scripts.sync_assistants_repartition --reverse   # 回滚：恢复问道中传独占
  .venv/bin/python -m scripts.sync_assistants_repartition --verify    # 只校验各标签点数

目标文档在 data 中均为问道中传独占，故新 assistants 数组 = ["问道中传", 标签]。
按标签分组，每组一次 set_payload(Filter doc_id MatchAny)，与 bulk_dept_sync 同模式。
向量零改动。
"""
import argparse
import collections
import json
from pathlib import Path

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from kb.common import COLLECTION, qdrant_client

BATCH = Path("batch-repartition-20261109")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reverse", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    c = qdrant_client(timeout=180)

    if args.verify:
        m = json.loads((BATCH / "repartition_manifest.json").read_text(encoding="utf-8"))
        labels = sorted({labs[0] for labs in m["additions"].values()})
        for lab in labels + ["问道中传"]:
            flt = Filter(must=[FieldCondition(key="assistants", match=MatchValue(value=lab))])
            n = c.count(collection_name=COLLECTION, count_filter=flt, exact=True).count
            print(f"  {lab}: {n} chunks")
        return

    if args.reverse:
        rev = json.loads((BATCH / "repartition_reverse.json").read_text(encoding="utf-8"))
        ids = rev["restore_exclusive"]
        flt = Filter(must=[FieldCondition(key="doc_id", match=MatchAny(any=ids))])
        c.set_payload(collection_name=COLLECTION, payload={"assistants": ["问道中传"]},
                      points=flt, wait=True)
        print(f"restored {len(ids)} docs to 问道中传-exclusive")
        return

    m = json.loads((BATCH / "repartition_manifest.json").read_text(encoding="utf-8"))
    groups = collections.defaultdict(list)
    for doc_id, labs in m["additions"].items():
        assert labs == [labs[0]] and labs[0] != "问道中传"
        groups[labs[0]].append(doc_id)
    total = 0
    for lab, ids in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        flt = Filter(must=[FieldCondition(key="doc_id", match=MatchAny(any=ids))])
        c.set_payload(collection_name=COLLECTION,
                      payload={"assistants": ["问道中传", lab]}, points=flt, wait=True)
        total += len(ids)
        print(f"  {lab}: {len(ids)} docs")
    print(f"assistants payload synced: {total} docs in {len(groups)} groups")


if __name__ == "__main__":
    main()