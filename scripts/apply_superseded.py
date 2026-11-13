#!/usr/bin/env python3
"""Apply verified supersedes relations to a data dir (item 3 version metadata).

Adds `superseded_by=<new_doc_id>` to every chunk (and the docs.json entry) of an
old regulation that a newer regulation explicitly 废止s. Ranking demotes the old
doc only when its replacement is also a candidate (kb/search_core.py), so the old
version stays retrievable for temporal queries. No Qdrant sync is needed: ranking
reads chunk_map from chunks.jsonl, not the vector payload.

  python -m scripts.apply_superseded --data data-v9            # dry-run, verify
  python -m scripts.apply_superseded --data data --apply       # patch + backup
  python -m scripts.apply_superseded --data data --reverse     # undo

Each relation is verified against the corpus before applying: the new doc must
have an active chunk containing both "废止" and the old doc's 文号.
"""

import argparse
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (old_doc_id, new_doc_id) — same-regulation supersessions verified from 废止
# clauses. Cross-regulation and compilation references are deliberately excluded.
RELATIONS = [
    ("d4ebb9c3ffa03", "de23f30ba9de9"),  # 本科生转专业管理办法 2024-77 -> 2025修订
    ("d30706b5f4da9", "dbf4b05614c57"),  # 财务管理办法 2015-75 -> 2024-324修订
    ("debff00767961", "ddbc013a4ebbf"),  # 研究生导师岗位聘任 2023-195 -> 2026-147
    ("dfdd429306176", "d2552d48de716"),  # 专业学位业界导师 2024-309 -> 2026-196
    ("d18e37b23b7de", "d97bbcae41b09"),  # 导师立德树人职责 2024-22 -> 2026-214
    ("dcae25d54d858", "d5d0532be07ce"),  # 硕士博士学位授予 2025-2 -> 2026-219
    ("d679f4b4e30bf", "ddf23271607a8"),  # 学位学历分离 2022-31 -> 2026-221
    ("d1ab68d2e1848", "d31d3cf629924"),  # 会议费管理办法 2019-194 -> 2023-220
    ("d6ad94c398349", "d8a4aa008a226"),  # 往来款项管理办法 2022-135 -> 2025-44
    ("d7f4df86d016d", "d406ceb4ca337"),  # 收费票据管理办法 2022-134 -> 2026-132
    ("d2e3bc13731a0", "d937e8e98636e"),  # 家庭经济困难学生认定 2019-77 -> 2020
    ("d23d27d58ca82", "d13a165c3144b"),  # 博士匿名评阅 2026-8 -> 2026-56
]


def norm(text):
    return re.sub(r"\s+", "", text or "")


def load(data_dir):
    docs = json.loads((data_dir / "docs.json").read_text(encoding="utf-8"))
    chunks = []
    with (data_dir / "chunks.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return docs, chunks


def verify(docs, chunks):
    doc_no = {d["doc_id"]: norm(d.get("doc_no", "")) for d in docs}
    title = {d["doc_id"]: d.get("title", "") for d in docs}
    new_text = {}
    for chunk in chunks:
        if chunk.get("status") == "active":
            new_text.setdefault(chunk["doc_id"], []).append(norm(chunk.get("text", "")))
    verified, rejected = [], []
    for old, new in RELATIONS:
        old_no = doc_no.get(old, "")
        texts = new_text.get(new, [])
        ok = bool(old_no) and any("废止" in t and old_no in t for t in texts)
        row = {"old": old, "new": new, "old_no": old_no,
               "old_title": title.get(old, "")[:50], "new_title": title.get(new, "")[:50]}
        (verified if ok else rejected).append(row)
    return verified, rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reverse", action="store_true")
    args = parser.parse_args()
    data_dir = ROOT / args.data
    docs, chunks = load(data_dir)
    verified, rejected = verify(docs, chunks)
    print(f"verified {len(verified)} relations, rejected {len(rejected)}")
    for row in rejected:
        print("  REJECT (no 废止 clause with old 文号):", row["old"], "->", row["new"], row["old_no"])
    for row in verified:
        print(f"  OK {row['old']} -> {row['new']} | {row['old_title']} => {row['new_title']}")
    if not args.apply and not args.reverse:
        print("dry-run; pass --apply to patch or --reverse to undo")
        return
    if rejected:
        raise SystemExit("refusing to apply with rejected relations; review first")

    old_ids = {row["old"] for row in verified}
    new_of = {row["old"]: row["new"] for row in verified}
    if args.reverse:
        for chunk in chunks:
            if chunk.get("doc_id") in old_ids:
                chunk.pop("superseded_by", None)
        for doc in docs:
            if doc.get("doc_id") in old_ids:
                doc.pop("superseded_by", None)
    else:
        for chunk in chunks:
            if chunk.get("doc_id") in old_ids:
                chunk["superseded_by"] = new_of[chunk["doc_id"]]
        for doc in docs:
            if doc.get("doc_id") in old_ids:
                doc["superseded_by"] = new_of[doc["doc_id"]]

    suffix = ".bak-superseded"
    for name in ("chunks.jsonl", "docs.json"):
        path = data_dir / name
        backup = Path(str(path) + suffix)
        if not args.reverse and not backup.exists():
            shutil.copy2(path, backup)
    with (data_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    (data_dir / "docs.json").write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
    affected = sum(1 for c in chunks if c.get("doc_id") in old_ids)
    print(f"{'reversed' if args.reverse else 'applied'} superseded_by on {affected} chunks / {len(old_ids)} docs")
    print(f"backups: {data_dir}/{{chunks.jsonl,docs.json}}{suffix}")


if __name__ == "__main__":
    main()