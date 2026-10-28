#!/usr/bin/env python3
"""Add the document-side 一卡通 alias to 校园卡管理办法 and re-embed those chunks."""
import json
import os
import shutil
import uuid
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

BASE = Path("/home/ubuntu/cuc-kb")
DATA = BASE / "data"
COLLECTION = "cuc_kb_v2"
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
CACHE = str(BASE / ".cache/fastembed")
ALIAS = "校园一卡通（一卡通）即校园卡。"
STAMP = "20261028-p1-alias"

chunks_path = DATA / "chunks.jsonl"
rows = [json.loads(line) for line in chunks_path.open(encoding="utf-8")]
targets = [c for c in rows if "校园卡管理办法" in c["doc_title"]]
if not targets:
    raise SystemExit("no 校园卡管理办法 chunks found")
changed = []
for chunk in targets:
    if chunk["embed_text"].startswith(ALIAS):
        continue
    chunk["embed_text"] = ALIAS + "\n" + chunk["embed_text"]
    changed.append(chunk)
print("targets", len(targets), "changed", len(changed))
if not changed:
    raise SystemExit("nothing to change")

backup = chunks_path.with_suffix(".jsonl.bak-" + STAMP)
if not backup.exists():
    shutil.copy2(chunks_path, backup)
tmp = chunks_path.with_suffix(".jsonl.tmp")
with tmp.open("w", encoding="utf-8") as handle:
    for chunk in rows:
        handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
os.replace(tmp, chunks_path)

model = TextEmbedding(model_name=EMBED_MODEL, cache_dir=CACHE)
vectors = np.vstack(
    [
        np.asarray(vector, dtype=np.float32)
        for vector in model.embed([c["embed_text"] for c in changed], batch_size=8)
    ]
)
norms = np.linalg.norm(vectors, axis=1, keepdims=True)
vectors = vectors / np.maximum(norms, 1e-12)

client = QdrantClient(
    url=os.environ.get("QDRANT_URL", "http://127.0.0.1:6333"),
    api_key=os.environ.get("QDRANT_API_KEY") or None,
    timeout=60,
)
points = []
for chunk, vector in zip(changed, vectors):
    payload = {
        key: value for key, value in chunk.items() if key != "embed_text"
    }
    points.append(
        PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk["chunk_id"])),
            vector={"dense": vector.tolist()},
            payload=payload,
        )
    )
client.upsert(collection_name=COLLECTION, points=points, wait=True)
print("upserted", len(points), "vectors")
