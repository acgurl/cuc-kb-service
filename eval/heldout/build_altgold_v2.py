#!/usr/bin/env python3
"""Build the alternate-gold labels for held-out v2 (dataset_heldout_v2_altgold.json).

The v2 set names one expected document per question, but the corpus often has
other active documents that state the same fact (compilations, FAQs, newer
editions, duplicates). Ranking one of those first is not a retrieval error, yet
it counts as a hit@1 miss. This script records those documents so the eval can
report a multi-gold score *next to* the frozen strict score:

* curated: active docs whose chunk text contains one of the case's verified
  answer keywords verbatim, minus candidates rejected by hand (REJECT below,
  each with the reason). Accepted candidates were read in context on
  2026-11-12 against data-v9.
  MANUAL_ADD lists true alternates that state the fact in other words.
* short: one-word queries have no single answer, so any active doc whose title
  contains the query term counts (deterministic rule, no hand edits).

The original dataset is not modified; strict metrics stay comparable with every
earlier run. Usage:

  python eval/heldout/build_altgold_v2.py [--data data-v9]
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "eval/heldout/dataset_heldout_v2.json"
OUT = ROOT / "eval/heldout/dataset_heldout_v2_altgold.json"

# Keyword-matching candidates that do NOT answer the question as asked.
# Value: doc_ids to drop ("*" = all candidates) and why.
# True alternates the verbatim rule misses (newer editions reworded the fact).
# Read in context 2026-11-12. Value: doc_ids and why.
MANUAL_ADD = {
    "v25": (["d4fd3b9a24abd"], "场馆管理规定：团体参观需至少提前2个工作日预约"),
    "v29": (
        ["d25321629ceab", "d4ecf4147655f"],
        "2025 查重检测办法：硕士总文字复制比（去除本人）应低于20%",
    ),
    "v31": (["d23d27d58ca82"], "2026 博士匿名评阅办法：最多可回避三名专家"),
}

REJECT = {
    "v10": ("*", "论证流程的专家人数，不是验收"),
    "v11": (["de63bd98fa100"], "实验室预约 APP 的房间联系人，不是设备借用"),
    "v31": ("*", "2022 汇编该段是硕士论文评阅回避，问的是博士"),
    "v43": (
        ["d438c8f7a92cf", "da5b715128d04", "d07f40c819929"],
        "寒假通知/研究生院流程里的同一号码，问的是暑假综合档案业务",
    ),
    "v61": ("*", "第四期简章，问的是第五期"),
    "v68": ("*", "校园卡初始密码，不是统一支付平台账号"),
    "v77": (["d7218c0e08f89"], "教师手册该段是访客预约，不是停车年度申报"),
    "v83": ("*", "党委宣传部审批讲座/横幅/校园媒体，不是国际加速专线"),
}


def norm(text):
    return re.sub(r"\s+", "", text or "")


def load_active(data_dir):
    docs = {d["doc_id"]: d for d in json.loads((data_dir / "docs.json").read_text(encoding="utf-8"))}
    chunks = []
    with (data_dir / "chunks.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            chunk = json.loads(line)
            if chunk.get("status") == "active":
                chunks.append((chunk["doc_id"], norm(chunk.get("text", ""))))
                # QA rows (doc_id "qa-csv") are not in docs.json; keep their title.
                docs.setdefault(chunk["doc_id"], {"title": chunk.get("doc_title", "")})
    active = {doc_id for doc_id, _ in chunks}
    return {k: v for k, v in docs.items() if k in active}, chunks


def is_expected(title, case):
    return any(e in title for e in case["expected_docs"])


def curated_alts(case, docs, chunks):
    keywords = [norm(k) for k in case.get("keywords", []) if norm(k)]
    found = []
    for doc_id, text in chunks:
        if doc_id in found or is_expected(docs[doc_id].get("title", ""), case):
            continue
        if any(k in text for k in keywords):
            found.append(doc_id)
    drop, reason = REJECT.get(case["id"], ([], ""))
    rejected = list(found) if drop == "*" else [d for d in found if d in drop]
    accepted = [d for d in found if d not in rejected]
    added, added_reason = MANUAL_ADD.get(case["id"], ([], ""))
    accepted += [d for d in added if d not in accepted]
    reason = "；".join(r for r in (reason, added_reason and "补充：" + added_reason) if r)
    return accepted, rejected, reason


def short_alts(case, docs):
    term = norm(case["query"])
    return [
        doc_id for doc_id, doc in docs.items()
        if term in norm(doc.get("title", "")) and not is_expected(doc.get("title", ""), case)
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data-v9")
    args = parser.parse_args()
    data_dir = ROOT / args.data
    docs, chunks = load_active(data_dir)
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))

    labels = {}
    for case in dataset["curated"]:
        accepted, rejected, reason = curated_alts(case, docs, chunks)
        if accepted or rejected:
            labels[case["id"]] = {
                "alt_doc_ids": accepted,
                "alt_titles": [docs[d].get("title", "") for d in accepted],
                "rejected_doc_ids": rejected,
                "note": reason or None,
            }
    for case in dataset["short"]:
        accepted = short_alts(case, docs)
        if accepted:
            labels[case["id"]] = {
                "alt_doc_ids": accepted,
                "alt_titles": [docs[d].get("title", "") for d in accepted],
            }

    out = {
        "meta": {
            "base": DATASET.name,
            "corpus": args.data,
            "rule_curated": "active doc containing a verified answer keyword verbatim, minus REJECT (hand review)",
            "rule_short": "active doc whose title contains the query term",
            "note": "strict metrics use expected_docs only; *_any metrics also accept alt_doc_ids",
        },
        "labels": labels,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    n_cur = sum(1 for k in labels if k.startswith("v") and labels[k]["alt_doc_ids"])
    n_short = sum(1 for k in labels if k.startswith("s"))
    print(f"curated cases with alternates: {n_cur}; short: {n_short}; saved {OUT}")


if __name__ == "__main__":
    main()
