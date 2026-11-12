#!/usr/bin/env python3
"""构建 data-v8：汇编版本收敛的元数据修正（向量零改动）。

改动仅两条文档（docs.json 与 chunks.jsonl 同步）：
  d896015a41752 中国传媒大学学生工作规章制度汇编2026-转换之后
      assistants += 中传教务君；publish_date=2026（标题年份）
  d6ac65db67ed2 2022年中国传媒大学学生工作规章制度汇编
      publish_date=2022（原 2025-11-18 是 jwc 政策页 2025-11-18 条目的错配）；
      is_generic=true（标题带年份前缀，旧规则漏判）

用法：
  python -m scripts.apply_v8_family --data-in data-v7 --data-out data-v8
向量不变，只改 payload 字段，可直接配合 scripts/sync_v8_family.py 上线。
"""
import argparse
import hashlib
import json
from pathlib import Path

CHANGES = {
    "d896015a41752": {
        "assistants": ["中传教务君", "新生帮手"],
        "publish_date": "2026",
        "date_basis": "标题年份",
    },
    "d6ac65db67ed2": {
        "publish_date": "2022",
        "date_basis": "标题年份",
        "is_generic": True,
    },
    # 中传学字〔2019〕224号 是含 16 个附件的老版资助办法汇编，内部附有
    # 2016 版《国家助学贷款管理办法》；按汇编降权，避免压过 2022/2026 现行版。
    "d3402a19c1e0a": {"is_generic": True},
}
# chunks.jsonl 不写 assistants 以外的文档级字段例外：两处保持一致即可。
CHUNK_FIELDS = ("assistants", "publish_date", "date_basis", "is_generic")


def _md5(path):
    h = hashlib.md5()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-in", default="data-v7")
    ap.add_argument("--data-out", default="data-v8")
    ap.add_argument("--manifest", default="版本收敛-20261110/manifest.json")
    args = ap.parse_args()

    src, dst = Path(args.data_in), Path(args.data_out)
    dst.mkdir(parents=True, exist_ok=True)

    docs = json.loads((src / "docs.json").read_text(encoding="utf-8"))
    before = {}
    for doc in docs:
        doc_id = doc.get("doc_id")
        if doc_id not in CHANGES:
            continue
        change = CHANGES[doc_id]
        before[doc_id] = {key: doc.get(key) for key in change}
        doc.update(change)
        if "assistants" in change:
            doc["assistants"] = sorted(set(doc["assistants"]))
    (dst / "docs.json").write_text(
        json.dumps(docs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    chunk_counts = {doc_id: 0 for doc_id in CHANGES}
    with (src / "chunks.jsonl").open(encoding="utf-8") as fin, (
        dst / "chunks.jsonl"
    ).open("w", encoding="utf-8") as fout:
        for line in fin:
            chunk = json.loads(line)
            doc_id = chunk.get("doc_id")
            change = CHANGES.get(doc_id)
            if change:
                for key, value in change.items():
                    if key in CHUNK_FIELDS:
                        chunk[key] = value
                if "assistants" in change:
                    chunk["assistants"] = sorted(set(change["assistants"]))
                chunk_counts[doc_id] += 1
            fout.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    manifest = {
        "source": str(src),
        "target": str(dst),
        "changes": CHANGES,
        "before": before,
        "chunk_counts": chunk_counts,
        "md5": {
            "docs.json": _md5(dst / "docs.json"),
            "chunks.jsonl": _md5(dst / "chunks.jsonl"),
        },
    }
    out = Path(args.manifest)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
