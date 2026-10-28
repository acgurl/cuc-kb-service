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

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

BASE = Path("/home/ubuntu/cuc-kb")
DATA = BASE / "data"
QA_CSV = BASE / "QA知识导入模板.csv"
STAMP = "20261028-p0"

GENERIC_PREFIXES = (
    "中国传媒大学学生工作规章制度汇编",
    "参考：教师手册",
    "修学指南2025版",
    "QA知识导入模板",
    "2026年迎接新生开学相关工作",
    "学校部门分类",
)

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


def is_generic_title(title: str) -> bool:
    return any(title.startswith(prefix) for prefix in GENERIC_PREFIXES)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def add_assistants(assistants, additions):
    result = list(assistants)
    for item in additions:
        if item not in result:
            result.append(item)
    return result


def qdrant_points_for_doc(client, doc_id):
    points = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name="cuc_kb_v2",
            scroll_filter=Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
            ),
            limit=512,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        points.extend(point.id for point in batch)
        if offset is None:
            break
    return points


def qdrant_points_for_chunk(client, chunk_id):
    batch, _ = client.scroll(
        collection_name="cuc_kb_v2",
        scroll_filter=Filter(
            must=[FieldCondition(key="chunk_id", match=MatchValue(value=chunk_id))]
        ),
        limit=10,
        with_payload=False,
        with_vectors=False,
    )
    return [point.id for point in batch]


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

    client = QdrantClient(
        url=os.environ.get("QDRANT_URL", "http://127.0.0.1:6333"),
        api_key=os.environ.get("QDRANT_API_KEY") or None,
        timeout=60,
    )

    for doc_id, assistants in affected_docs.items():
        points = qdrant_points_for_doc(client, doc_id)
        if points:
            client.set_payload(
                collection_name="cuc_kb_v2",
                payload={"assistants": assistants},
                points=points,
                wait=True,
            )
        print("qdrant assistant:", doc_id, len(points), assistants)

    for doc_id in generic_docs:
        points = qdrant_points_for_doc(client, doc_id)
        if points:
            client.set_payload(
                collection_name="cuc_kb_v2",
                payload={"is_generic": True},
                points=points,
                wait=True,
            )
        print("qdrant generic:", doc_id, len(points))

    qa_points = qdrant_points_for_doc(client, "qa-csv")
    if qa_points:
        client.set_payload(
            collection_name="cuc_kb_v2",
            payload={"is_generic": True},
            points=qa_points,
            wait=True,
        )
    print("qdrant qa generic:", len(qa_points))

    stale_points = qdrant_points_for_chunk(client, STALE_QA_CHUNK)
    if stale_points:
        client.set_payload(
            collection_name="cuc_kb_v2",
            payload={"status": "inactive", "status_reason": STALE_QA_REASON},
            points=stale_points,
            wait=True,
        )
    print("qdrant stale qa:", STALE_QA_CHUNK, len(stale_points))
    print("applied")


if __name__ == "__main__":
    main()
