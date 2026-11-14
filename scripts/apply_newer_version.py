#!/usr/bin/env python3
"""Link older editions of periodically reissued documents to the newest edition.

Yearly notices, explanations and agreements are reissued under the same title
without a 废止 clause, so scripts/apply_superseded.py cannot cover them. This
adds `newer_version=<new_doc_id>` to every chunk (and the docs.json entry) of an
older edition. With KB_NEWER_VERSION=1 the old edition yields to the new one when
both are candidates and its citation is flagged (kb/search_core.py); it stays
fully searchable, and a query naming the old edition's year is not demoted. No
Qdrant sync is needed: ranking reads chunk_map from chunks.jsonl.

  python -m scripts.apply_newer_version --data data-live            # dry-run, verify
  python -m scripts.apply_newer_version --data data --apply         # patch + backup
  python -m scripts.apply_newer_version --data data --reverse       # undo

Each relation is verified before applying: both docs have active chunks, share
the same core title (year, hash suffix and 附件 prefix stripped), the old one is
not already superseded_by something, and the new one is dated later.
"""

import argparse
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (old_doc_id, new_doc_id), reviewed 2026-11-14 against data-live. Deliberately
# excluded same-title pairs: 科级干部聘任管理办法 (2018 entry is a stub page, old
# content unique), 财务、资产及收费信息 (different yearly content), and pairs
# where one side has no date.
RELATIONS = [
    # 校园地国家助学贷款情况的说明 2021-2025 -> 2026
    ("de24d701a61c2", "d6ad263a3ad04"),
    ("da0cbf89edbfa", "d6ad263a3ad04"),
    ("d89e8b4543ec9", "d6ad263a3ad04"),
    ("dfa496461f80b", "d6ad263a3ad04"),
    ("debb1936ab76e", "d6ad263a3ad04"),
    # 校运会开幕式期间校内交通临时管制的通知 2023-2025 -> 2026
    ("d52c60e48a08c", "d02cd78695ff6"),
    ("dc7a769f34d57", "d02cd78695ff6"),
    ("db83afd08ba2e", "d02cd78695ff6"),
    # 研究生学位论文编写规则 2021 -> 2025（人文社科版 / 自然科学版）
    ("de4d109ffa8d8", "de36f577a5168"),
    ("da05388451db2", "de36f577a5168"),
    ("d160e47d72cf6", "d14a29cae1641"),
    ("d7f1de7c1d22d", "d14a29cae1641"),
    # 招生专业介绍页 2021/2022 -> 2023
    ("d2ec9b2f2de55", "d6c3f0b64caab"),
    ("d5670da2d1da1", "d6c3f0b64caab"),
    ("d45353043fe12", "d4c79068e11c1"),
    ("d17ade3902cb1", "d5ea4a4c3a130"),  # 双师型师资培训班招生简章 2023 -> 2024
    ("db476f6cdc1f4", "d8576e24d7edc"),  # 学生公寓住宿协议书 2024 -> 2026
    ("d910956922acf", "d45f0178fa6c6"),  # 档案法 2018 页 -> 2021 施行的修订版
    ("d6eb74e5b388d", "d85dc9366d5e2"),  # 研究生外语水平验证说明 2025 -> 2026
    ("d5a2a24e7dd3d", "d45dfd136bbb3"),  # 港澳台研究生初试成绩复核申请单 2025 -> 2026
    ("d632c56d1b337", "d5611c9193b35"),  # 校园统一支付平台报名缴费流程 2023 -> 2024
]

FIELD = "newer_version"


def core_title(title):
    title = re.sub(r"_[0-9a-f]{6}$", "", title or "")
    title = re.sub(r"\.(docx?|pdf|xlsx?)(_view)?$", "", title)
    title = re.sub(r"^附件\s*\d*[\.．、:：]?\s*", "", title)
    title = re.sub(r"_\d{4}-\d{2}-\d{2}$", "", title)
    title = re.sub(r"(19|20)\d{2}", "#", title)
    return re.sub(r"[\s()（）【】\[\]“”\"、，,：:_\-—.]+", "", title)


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
    meta = {d["doc_id"]: d for d in docs}
    active, superseded = set(), set()
    for chunk in chunks:
        if chunk.get("status") == "active":
            active.add(chunk["doc_id"])
        if chunk.get("superseded_by"):
            superseded.add(chunk["doc_id"])
    old_ids = {old for old, _ in RELATIONS}
    verified, rejected = [], []
    for old, new in RELATIONS:
        old_doc, new_doc = meta.get(old), meta.get(new)
        reason = ""
        if not old_doc or not new_doc:
            reason = "doc missing"
        elif old not in active or new not in active:
            reason = "no active chunks"
        elif old in superseded:
            reason = "old already superseded_by"
        elif new in old_ids or new in superseded:
            reason = "target is itself an old edition"
        elif core_title(old_doc.get("title")) != core_title(new_doc.get("title")):
            reason = "core titles differ"
        elif not old_doc.get("publish_date") or not new_doc.get("publish_date"):
            reason = "missing publish_date"
        elif str(old_doc["publish_date"]) >= str(new_doc["publish_date"]):
            reason = "old is not dated earlier"
        row = {
            "old": old, "new": new, "reason": reason,
            "old_date": (old_doc or {}).get("publish_date", ""),
            "new_date": (new_doc or {}).get("publish_date", ""),
            "title": ((new_doc or {}).get("title") or "")[:40],
        }
        (rejected if reason else verified).append(row)
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
        print(f"  REJECT ({row['reason']}): {row['old']} -> {row['new']}")
    for row in verified:
        print(f"  OK {row['old']} ({row['old_date']}) -> {row['new']} ({row['new_date']}) | {row['title']}")
    if not args.apply and not args.reverse:
        print("dry-run; pass --apply to patch or --reverse to undo")
        return
    if rejected:
        raise SystemExit("refusing to apply with rejected relations; review first")

    new_of = {row["old"]: row["new"] for row in verified}
    for record in (*chunks, *docs):
        if record.get("doc_id") in new_of:
            if args.reverse:
                record.pop(FIELD, None)
            else:
                record[FIELD] = new_of[record["doc_id"]]

    suffix = ".bak-newerversion"
    for name in ("chunks.jsonl", "docs.json"):
        path = data_dir / name
        backup = Path(str(path) + suffix)
        if not args.reverse and not backup.exists():
            shutil.copy2(path, backup)
    with (data_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    (data_dir / "docs.json").write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
    affected = sum(1 for c in chunks if c.get("doc_id") in new_of)
    print(f"{'reversed' if args.reverse else 'applied'} {FIELD} on {affected} chunks / {len(new_of)} docs")
    print(f"backups: {data_dir}/{{chunks.jsonl,docs.json}}{suffix}")


if __name__ == "__main__":
    main()
