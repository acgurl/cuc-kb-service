#!/usr/bin/env python3
"""Build dataset_evidence_v3_1.json = evidence v3 + ground-truth corrections.

v3's first live run (2026-11-14) showed annotation errors, not system errors:
unanswerable cases whose fact is in the corpus, one outdated gold answer, one
gold taken from an older regulation, and under-specified questions. This
script applies an explicit, reviewed patch list to v3 and re-verifies every
changed case against the corpus. v3 itself is left untouched.

  python eval/heldout/build_evidence_v3_1.py [--data data-live]
"""

import argparse
import copy
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def norm(text):
    return re.sub(r"\s+", "", text or "")


# Unanswerable in v3, but the asked fact is (or arguably is) in the corpus.
# Removed rather than converted: the same phone number / clause appears in
# several documents, so a single-doc evidence item would be an unfair target.
REMOVE = {
    "b14b_08": "教育基金会咨询电话 010－65783934 在《捐赠办理流程》等三份流程文件中逐字存在",
    "b17bp02": "档案馆综合档案科电话 010-65779232 在《档案馆部门职责情况》及寒假业务通知中存在",
    "b17bp06": "国际交流与合作处综合科电话 65783433 在教师手册通讯录与 QA 中存在",
    "b17bp08": "督导处质量建设科电话 010-65783909 在《教师版—课程质量评价Q&A》中存在",
    "b17bp10": "研究生院教学运行与学籍管理科电话 010-65779643 在部门介绍中存在",
    "b12_12": "各类档案管理规范附件注意事项写明借出档案原件须两周内归还",
    "b17at02": "绩效工资实施办法写明常规督导听课核拨标准 150 元/课时",
    "b8_12": "2018 研究生手册写明专家组 30 日内认定（旧手册，是否现行不明，属歧义题）",
    "b13_12": "2016 年留学生招生简章列有留学生公寓日收费标准（过时但存在，属歧义题）",
    "b17bp03": "《学生公寓校园网建设的公告》列有两种征求意见的计费方案（属歧义题）",
    "b18na15": "出国留学材料办理说明含中英文成绩单与证书翻译件办理（属歧义题）",
    "b9_12": "国有资产使用管理办法写明出租价格参照市场价或评估价（问的是怎么定，属可答）",
    "b1_12": "国际研究生注册地点（学生国际交流部）在库，普通研究生无地点（属歧义题）",
}

# Under-specified questions: the gold answer targets one object, the question
# fits several. Only a qualifier is added; gold answer and evidence unchanged.
REQUERY = {
    "b16b09": "场馆违约几次拉黑",  # was 违约几次进黑名单 (图书馆 7 次 vs 体育场馆 2 次)
    "b16b10": "团体订场提前几天",  # was 团体预约提前几天 (博物馆参观 vs 体育场地)
    "b14b_07": "MBA预科学费",  # was MBA学费多少 (gold is the 预科项目)
    "b1_01": "研究生什么情况可以申请缓考？批下来之后成绩怎么记？",
    "b4b_01": "2026年艺术类校考的初试安排在哪天？考完之后什么时候能查到初试成绩？",
    "b4b_03": "2026年校考什么时候开始报名？最多能同时报几个专业？报名要符合什么条件？",
}

LOAN_TERMS = ["每人每年申请贷款额度不超过20000元", "每人每年申请贷款额度不超过25000元"]

REGOLD = {
    # v3 gold 16000/20000 came from the 2024-06 说明; 财教〔2024〕188号 raised the
    # caps and the 2025/2026 说明 and 2026 入学须知 carry 20000/25000.
    "vc01": {
        "answer": "按现行的说明，全日制普通本科学生每人每年申请贷款额度不超过20000元，全日制研究生每人每年申请贷款额度不超过25000元。",
        "required_points": ["本科不超过20000元", "研究生不超过25000元"],
        "evidence": [{"doc_id": "d6ad263a3ad04", "chunk_id": "d6ad263a3ad04-0000", "terms": LOAN_TERMS}],
        "watch_docs": ["de24d701a61c2", "d89e8b4543ec9", "dfa496461f80b"],
        "note": "v3.1 更正：2021 版 8000/12000、2023 版 12000/16000、2024 版 16000/20000 三份旧说明均 active 未标废止；"
                "财教〔2024〕188号提额后现行为 20000/25000（2025、2026 版说明与 2026 入学须知）。",
    },
    # v3 gold came from 中传教字〔2006〕103号 (web date 2019); 中传人字〔2017〕208号 is
    # the later school-level regulation for the same award. Neither is marked
    # superseded and 208号 has no repeal clause, so this is a version conflict.
    "cov02": {
        "category": "version_conflict",
        "answer": "按2017年印发的现行办法，优秀教学奖每两年评选一次，一等奖3名、二等奖5名、三等奖10名；奖金一等奖2万元/人、二等奖1万元/人、三等奖5000元/人。",
        "required_points": ["每两年评选一次", "一等奖3名、二等奖5名、三等奖10名", "一等奖2万元、二等奖1万元、三等奖5000元"],
        "evidence": [{"doc_id": "ddf6207acb322", "chunk_id": "ddf6207acb322-0007",
                      "terms": ["优秀教学奖每两年评选一次", "一等奖奖金2 万元/人", "三等奖奖金5000 元/人"]}],
        "watch_docs": ["d91d14a5a1c33"],
        "note": "v3.1 更正并改类：原标答取自中传教字〔2006〕103号（每年一次、15名、5000/3000/1500），"
                "中传人字〔2017〕208号为同一奖项的后出校级办法；两份均 active、208号无废止条款，现行版本待人工确认。",
    },
}


def load_corpus(data_dir):
    chunks = {}
    with (data_dir / "chunks.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            chunk = json.loads(line)
            chunks[chunk["chunk_id"]] = chunk
    return chunks


def active(chunk):
    return chunk.get("status", "active") == "active"


def current_texts(chunks):
    """(doc_id, normalized text) of every active, non-superseded chunk."""
    return [
        (chunk["doc_id"], norm(chunk.get("text", "")))
        for chunk in chunks.values()
        if active(chunk) and not chunk.get("superseded_by")
    ]


def alt_docs_for(item, texts, exclude=()):
    """Other active, non-superseded docs with one chunk holding all terms."""
    terms = [norm(t) for t in item["terms"]]
    skip = {item["doc_id"], *exclude}
    return sorted({
        doc_id for doc_id, text in texts
        if doc_id not in skip and all(term in text for term in terms)
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data-live")
    parser.add_argument(
        "--alt-all", action="store_true",
        help="also write dataset_evidence_v3_1_alt.json: alt_docs on every evidence item "
             "(any current doc holding the same verbatim terms counts; watch_docs excluded)",
    )
    args = parser.parse_args()
    chunks = load_corpus(ROOT / args.data)
    texts = current_texts(chunks)
    source = json.loads((HERE / "dataset_evidence_v3.json").read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in source["cases"]}
    errors = []

    for key in list(REMOVE) + list(REQUERY) + list(REGOLD):
        if key not in cases:
            errors.append(f"{key}: not in v3")
    for key in REMOVE:
        if cases.get(key, {}).get("answerable", True):
            errors.append(f"{key}: REMOVE is only for unanswerable cases")

    used = set(json.loads((HERE / "_v3_used_chunks.json").read_text(encoding="utf-8")))
    taken_queries = {norm(q) for q in json.loads((HERE / "_v3_existing_queries.json").read_text(encoding="utf-8"))}
    taken_queries |= {norm(c["query"]) for c in source["cases"]}
    v3_evidence_chunks = {e["chunk_id"] for c in source["cases"] for e in c.get("evidence", [])}

    out = []
    for case in source["cases"]:
        cid = case["id"]
        if cid in REMOVE:
            continue
        case = copy.deepcopy(case)
        if cid in REQUERY:
            new_query = REQUERY[cid]
            if norm(new_query) in taken_queries:
                errors.append(f"{cid}: new query duplicates an existing query")
            if case["category"] == "short_query" and len(norm(new_query)) > 8:
                errors.append(f"{cid}: short_query longer than 8 chars")
            case["query_v3"] = case["query"]
            case["query"] = new_query
        if cid in REGOLD:
            old_chunks = {e["chunk_id"] for e in case.get("evidence", [])}
            case.update(copy.deepcopy(REGOLD[cid]))
            for item in case["evidence"]:
                chunk = chunks.get(item["chunk_id"])
                if not chunk or not active(chunk) or chunk.get("superseded_by"):
                    errors.append(f"{cid}: evidence chunk {item['chunk_id']} missing/inactive/superseded")
                    continue
                text = norm(chunk.get("text", ""))
                for term in item["terms"]:
                    if norm(term) not in text:
                        errors.append(f"{cid}: term not verbatim in {item['chunk_id']}: {term}")
                if item["chunk_id"] in used:
                    errors.append(f"{cid}: evidence chunk reused from v1/v2")
                if item["chunk_id"] in v3_evidence_chunks - old_chunks:
                    errors.append(f"{cid}: evidence chunk already used by another v3 case")
                alts = alt_docs_for(item, texts, case.get("watch_docs") or ())
                if alts:
                    item["alt_docs"] = alts
            evidence_docs = {d for e in case["evidence"] for d in [e["doc_id"], *e.get("alt_docs", [])]}
            for doc in case.get("watch_docs", []):
                if doc in evidence_docs:
                    errors.append(f"{cid}: watch doc {doc} is also an evidence doc")
                if not any(c["doc_id"] == doc and active(c) for c in chunks.values()):
                    errors.append(f"{cid}: watch doc {doc} has no active chunk")
        out.append(case)

    if errors:
        print("\n".join(errors))
        sys.exit(1)

    counts = {}
    for case in out:
        counts[case["category"]] = counts.get(case["category"], 0) + 1
    answerable = sum(1 for c in out if c.get("answerable", True))
    meta = dict(source["meta"])
    meta.update({
        "name": "evidence_v3_1",
        "based_on": "evidence_v3",
        "counts": counts,
        "answerable": answerable,
        "unanswerable": len(out) - answerable,
        "v3_1_changes": {
            "removed": REMOVE,
            "requery": {k: {"from": cases[k]["query"], "to": v} for k, v in REQUERY.items()},
            "regold": sorted(REGOLD),
            "note": "v3 首轮线上实测后的标注更正；改动均为标注错误修复，非按系统输出调题。"
                    "被改问句与标答的 8 题不再是严格盲测题。",
        },
    })
    meta.pop("domain_coverage", None)
    target = HERE / "dataset_evidence_v3_1.json"
    target.write_text(json.dumps({"meta": meta, "cases": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {target.name}: {len(out)} cases ({answerable} answerable), counts={counts}")

    if args.alt_all:
        added = 0
        for case in out:
            for item in case.get("evidence", []):
                if "alt_docs" in item:
                    continue
                alts = alt_docs_for(item, texts, case.get("watch_docs") or ())
                if alts:
                    item["alt_docs"] = alts
                    added += 1
        meta_alt = dict(meta, name="evidence_v3_1_alt",
                        alt_note="多来源口径：任一现行文档逐字含全部证据词即算命中；严格口径见 dataset_evidence_v3_1.json")
        alt_target = HERE / "dataset_evidence_v3_1_alt.json"
        alt_target.write_text(json.dumps({"meta": meta_alt, "cases": out}, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        print(f"wrote {alt_target.name}: alt_docs added to {added} more evidence items")


if __name__ == "__main__":
    main()
