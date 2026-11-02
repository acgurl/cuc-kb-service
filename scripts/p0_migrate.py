#!/usr/bin/env python3
"""P0 metadata migration: assistant assignments, generic-doc flags, stale QA.

Run with --apply to write; default is a dry run.
"""
import argparse
import csv
import json
import os
import shutil
from pathlib import Path

from qdrant_client.models import FieldCondition, Filter, MatchValue

from kb.common import BASE_DIR, COLLECTION, DATA_DIR, is_generic_title, qdrant_client
from kb.ingest import merge_unique

DATA = DATA_DIR
QA_CSV = BASE_DIR / "QA知识导入模板.csv"
STAMP = "20261028-p0"

ASSISTANT_ADDITIONS = {
    # doc_id -> assistants to add
    "d26280db90637": ["财务处小助手"],  # 中传财字 [2019] 35号 差旅费
    "d68788bbbd78b": ["财务处小助手"],  # 中传财字 [2019] 175号 科研经费报销
    "d98f77aa1a035": ["问道中传", "新生帮手"],  # 知识库-学生公寓服务中心
    "d2dd9cf99f893": ["新生帮手", "中传小勤"],  # 知识库-后勤服务指南
    "dcb26276703b5": ["中传小勤"],  # 知识库-校园管理中心
    "d17ca458cb49e": ["中传小勤"],  # 知识库-饮食服务中心
}

STALE_QA_CHUNK = "qa-0013"
STALE_QA_QUESTION = "保研（推免）的相关规定在哪里？"
STALE_QA_REASON = "stale: 2026-122 推免办法已入库，旧答案称尚未发布"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def add_assistants(assistants, additions):
    result = list(assistants)
    merge_unique(result, additions)
    return result


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
        points.extend(point.id for point in batch)
        if offset is None:
            return points


def set_payload(client, key, value, payload):
    points = point_ids(client, key, value)
    if points:
        client.set_payload(
            collection_name=COLLECTION, payload=payload, points=points, wait=True
        )
    return len(points)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    docs = load_json(DATA / "docs.json")
    chunks = [json.loads(line) for line in (DATA / "chunks.jsonl").open(encoding="utf-8")]

    affected_docs = {}
    generic_docs = set()
    for doc in docs:
        doc_id = doc["doc_id"]
        if is_generic_title(doc["title"]):
            doc["is_generic"] = True
            generic_docs.add(doc_id)
        else:
            doc["is_generic"] = False
        additions = ASSISTANT_ADDITIONS.get(doc_id)
        if additions:
            before = list(doc.get("assistants") or [])
            doc["assistants"] = add_assistants(before, additions)
            affected_docs[doc_id] = doc["assistants"]
            print("doc:", doc["title"][:60], before, "->", doc["assistants"])

    for chunk in chunks:
        chunk["is_generic"] = bool(chunk.get("is_generic")) or is_generic_title(
            chunk.get("doc_title", "")
        )
        if chunk["doc_id"] in affected_docs:
            chunk["assistants"] = list(affected_docs[chunk["doc_id"]])
        if chunk["chunk_id"] == STALE_QA_CHUNK:
            chunk["status"] = "inactive"
            chunk["status_reason"] = STALE_QA_REASON
            print("qa:", chunk["chunk_id"], STALE_QA_QUESTION, "-> inactive")

    qa_rows = list(csv.DictReader(QA_CSV.open(encoding="utf-8-sig", newline="")))
    remaining = [row for row in qa_rows if (row.get("问题") or "").strip() != STALE_QA_QUESTION]
    print("qa csv rows:", len(qa_rows), "->", len(remaining))

    print("generic docs:", len(generic_docs), sorted(generic_docs))
    for doc_id in generic_docs:
        print("  generic:", next(d["title"] for d in docs if d["doc_id"] == doc_id))

    print(f"collection={COLLECTION} data={DATA} qa={QA_CSV}")
    if not args.apply:
        print("dry run; pass --apply to write")
        return

    docs_path = DATA / "docs.json"
    chunks_path = DATA / "chunks.jsonl"
    for source, backup in (
        (docs_path, docs_path.with_suffix(".json.bak-" + STAMP)),
        (chunks_path, chunks_path.with_suffix(".jsonl.bak-" + STAMP)),
        (QA_CSV, QA_CSV.with_suffix(".csv.bak-" + STAMP)),
    ):
        if not backup.exists():
            shutil.copy2(source, backup)

    docs_tmp = docs_path.with_suffix(".json.tmp")
    docs_tmp.write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(docs_tmp, docs_path)

    chunks_tmp = chunks_path.with_suffix(".jsonl.tmp")
    with chunks_tmp.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    os.replace(chunks_tmp, chunks_path)

    headers = ["问题", "答案"]
    qa_tmp = QA_CSV.with_suffix(".csv.tmp")
    with qa_tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(remaining)
    os.replace(qa_tmp, QA_CSV)

    client = qdrant_client(timeout=60)
    for doc_id, assistants in affected_docs.items():
        count = set_payload(client, "doc_id", doc_id, {"assistants": assistants})
        print("qdrant assistant:", doc_id, count, assistants)
    for doc_id in generic_docs:
        print("qdrant generic:", doc_id, set_payload(client, "doc_id", doc_id, {"is_generic": True}))
    print("qdrant qa generic:", set_payload(client, "doc_id", "qa-csv", {"is_generic": True}))
    stale = set_payload(
        client,
        "chunk_id",
        STALE_QA_CHUNK,
        {"status": "inactive", "status_reason": STALE_QA_REASON},
    )
    print("qdrant stale qa:", STALE_QA_CHUNK, stale)
    print("applied")


if __name__ == "__main__":
    main()
