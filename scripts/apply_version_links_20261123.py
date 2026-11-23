#!/usr/bin/env python3
"""Link additional verified multi-edition families found in Nov 22-23 agent logs.

Adds `newer_version=<new_doc_id>` to older editions in `chunks.jsonl` and
`docs.json`, and corrects the three `2022/2023/2024修学指南电子版` publish_date
fields that carried the 2024-11-25 crawl date instead of their edition year.
No Qdrant sync is needed: ranking and filtering read chunk_map from chunks.jsonl.

  python -m scripts.apply_version_links_20261123 --data data-live        # dry-run
  python -m scripts.apply_version_links_20261123 --data data --apply     # patch + backup
  python -m scripts.apply_version_links_20261123 --data data --reverse   # undo
"""

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (old_doc_id, new_doc_id, note), verified against data-live and Phoenix prod 2026-11-23.
RELATIONS = [
    # 修学指南 2022-2024 -> 2025
    ("d9d73cd38ee5f", "d7bc0472b2e0e", "2022修学指南 -> 2025版"),
    ("d9b2a4335370c", "d7bc0472b2e0e", "2023修学指南 -> 2025版"),
    ("dd8d93f011636", "d7bc0472b2e0e", "2024修学指南 -> 2025版"),
    # 新生入学须知（按校区与学历分别指向最新一届）
    ("dff69a6b26521", "d102d83462014", "2020级新生入学须知 -> 2026级本科生入学须知（校本部）"),
    ("dee7fd0ac5286", "dfe84c2390605", "2021级研究生入学须知 -> 2026级研究生入学须知"),
    ("d147eca556eeb", "d30283251afe2", "2022级海南入学须知 -> 2026级本科生入学须知（海南）"),
    ("da718d80da6f2", "d3abc56956cb4", "2023年海南研究生入学须知 -> 2025级海南研究生入学须知"),
    # 研究生学位论文编写规则（补漏的 2022.9 版）
    ("da33aded82dc9", "de36f577a5168", "人文社科版2022.9更新版 -> 2025-11版"),
    # 研究生学籍管理规定 2020级/2021/2022 -> 中传研字〔2024〕159号
    ("d3cdd15548400", "dc6abc3c87568", "适用2020级 -> 中传研字〔2024〕159号"),
    ("d9a769daee9ce", "dc6abc3c87568", "中传研字〔2021〕105 -> 中传研字〔2024〕159号"),
    ("d501b6e7c8ffd", "dc6abc3c87568", "中传研字〔2022〕35号 -> 中传研字〔2024〕159号"),
    # 中传邮箱升级&自助开通指南 2022/2025-04 -> 2025-07
    ("dbddf97e91ab8", "df97c311e4767", "邮箱自助开通指南2022 -> 2025-07"),
    ("d2bdf32178071", "df97c311e4767", "邮箱自助开通指南2025-04 -> 2025-07"),
    # 全库扫描补充：按年度/学期重发且双方正文完整的 17 对
    ("d83c2e19b68f2", "d9993dd91a9cd", "2025年辅修招生信息 -> 2026年"),
    ("d4fad8358db62", "d330a27194b07", "2024中国画博士班招生简章 -> 2026年"),
    ("de587abf62cd2", "d68c9fe334c3b", "2024年度研究生导师岗位聘任条件 -> 2025年度"),
    ("d0eaeb9e1cf67", "d58b69e84ff28", "2024专业学位硕士研究生导师评聘条件 -> 2026年"),
    ("d00eeabc708c3", "d2d8e7c4b6c2f", "攻读学术学位硕士研究生培养环节流程2021 -> 2024"),
    ("d3a21d2237493", "dc12a98f0b3fe", "研究生公共外语课程教学管理办法2023 -> 2024"),
    ("d12df543bc1b1", "dc59766cf8c47", "2026春季服兵役国家教育资助 -> 2026秋季"),
    ("dc39490fae186", "df12a321debcf", "2025-2026春开学初补退选课 -> 2026-2027秋"),
    ("db180effec0b4", "dd50c2005ab58", "2024-2025春课程质量自评与学院评价 -> 2025-2026秋"),
    ("da8d3c163c5e7", "d2725c33b0a0f", "2023-2024春重点督导课程推荐 -> 2025-2026春"),
    ("da7c53b6ae33d", "d2725c33b0a0f", "2024-2025秋重点督导课程推荐 -> 2025-2026春"),
    ("da3af4add0ae2", "d2725c33b0a0f", "2025-2026秋重点督导课程推荐 -> 2025-2026春"),
    ("da16f49254683", "d0c96e176324c", "2011教师赴国内外进修培养计划 -> 2014年"),
    ("d89dee3e49577", "d16348a6f11cd", "2021国家留学基金资助出国留学 -> 2023年"),
    ("d05a3c2263cfc", "db759a03e0c5e", "校医院2025-2026二公费医疗报销安排 -> 2026-2027一"),
    ("d44041fa4c2d6", "d91ffe1034c41", "2024-2025学年学生缴费通知 -> 2025-2026学年"),
    ("d289c6533047a", "d64f11135b8ca", "新生必看信息化服务指南2022 -> 2026"),
]

# doc_id -> (fixed_publish_date, original_publish_date)
DATE_FIXES = {
    "d9d73cd38ee5f": ("2022", "2024-11-25"),
    "d9b2a4335370c": ("2023", "2024-11-25"),
    "dd8d93f011636": ("2024", "2024-11-25"),
}

FIELD = "newer_version"


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
    old_ids = {old for old, _, _ in RELATIONS}
    verified, rejected = [], []
    for old, new, note in RELATIONS:
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
        row = {
            "old": old, "new": new, "note": note, "reason": reason,
            "old_date": (old_doc or {}).get("publish_date", ""),
            "new_date": (new_doc or {}).get("publish_date", ""),
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
        print(f"  REJECT ({row['reason']}): {row['old']} -> {row['new']} | {row['note']}")
    for row in verified:
        print(f"  OK {row['old']} ({row['old_date']}) -> {row['new']} ({row['new_date']}) | {row['note']}")
    if not args.apply and not args.reverse:
        print("dry-run; pass --apply to patch or --reverse to undo")
        return
    if rejected:
        raise SystemExit("refusing to apply with rejected relations; review first")

    new_of = {row["old"]: row["new"] for row in verified}
    for record in (*chunks, *docs):
        doc_id = record.get("doc_id")
        if doc_id in new_of:
            if args.reverse:
                record.pop(FIELD, None)
            else:
                record[FIELD] = new_of[doc_id]
        if doc_id in DATE_FIXES:
            fixed, original = DATE_FIXES[doc_id]
            record["publish_date"] = original if args.reverse else fixed

    suffix = ".bak-vlinks-20261123"
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
