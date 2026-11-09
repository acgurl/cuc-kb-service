#!/usr/bin/env python3
"""Rebuild the no-answer set against the 2026-11-07 corpus (data-v3).

The v1 set was built on the 311-doc corpus; several probes now hit the
official-site batch (e.g. 附属医院/四六级/运动会). This script re-validates every
v1 draft (noanswer v1 + heldout v1) plus new drafts against data-v3 active
chunks (text + title, whitespace-normalized). Zero-hit probes are kept;
hits are dropped and logged. Old manual-review verdicts carry over:
"answerable" drafts stay dropped; "partial" drafts stay partial only while
their probes still have zero hits.

Output: eval/heldout/dataset_noanswer_v2.json
"""
import json
import re
from pathlib import Path

from build_noanswer import CASES as V1_CASES, REVIEW
from build_heldout import NO_ANSWER as HELDOUT_NA

ROOT = Path(__file__).resolve().parents[2]

# (id, category, query, probes) — new drafts for the enlarged corpus
NEW_CASES = [
    ("nb01", "campus", "校医院周末能拍X光片吗？", ["拍X光", "X光片"]),
    ("nb02", "campus", "留学生宿舍能自己做饭吗？", ["留学生宿舍", "宿舍做饭"]),
    ("nb03", "campus", "学校有没有母婴室？", ["母婴室"]),
    ("nb04", "campus", "校内哪里能配钥匙？", ["配钥匙"]),
    ("nb05", "campus", "操场跑道一圈多少米？", ["跑道一圈"]),
    ("nb06", "campus", "宿舍楼电梯晚上几点停运？", ["电梯停运"]),
    ("nb07", "campus", "学校快递柜超时取件收费吗？", ["快递柜", "超时取件"]),
    ("nb08", "campus", "校内能飞无人机航拍吗？", ["无人机"]),
    ("nb09", "campus", "学校浴室插卡出水怎么计费？", ["插卡出水", "浴室计费"]),
    ("nb10", "adjacent", "转专业面试一般考什么内容？", ["转专业面试"]),
    ("nb11", "adjacent", "重修一门课要交多少重修费？", ["重修费"]),
    ("nb12", "adjacent", "学校承认雅思成绩免修大学英语吗？", ["雅思免修", "免修英语"]),
    ("nb13", "adjacent", "本科生毕业论文开题报告要写多少字？", ["开题报告字数", "开题多少字"]),
    ("nb14", "adjacent", "学生证火车票优惠卡一年充几次？", ["优惠卡", "火车票优惠"]),
    ("nb15", "adjacent", "休学创业最多能休几年？", ["休学创业"]),
    ("nb16", "external", "北京市每年给高校多少落户指标？", ["落户指标"]),
    ("nb17", "external", "考研政治科目怎么复习比较好？", ["考研政治"]),
    ("nb18", "external", "天津的高校承认我校辅修学位吗？", ["天津的高校"]),
    ("nb19", "instance", "下周五教务处开什么会？", ["下周五"]),
    ("nb20", "instance", "今天食堂推出了什么新菜？", ["新菜"]),
    ("nb21", "instance", "昨晚学校停电是因为什么？", ["昨晚学校停电"]),
    ("nb22", "personal", "我的体测成绩是多少分？", ["我的体测"]),
    ("nb23", "personal", "我这学期的课表在哪看？", ["我的课表", "我这学期"]),
    ("nb24", "offtopic", "周杰伦演唱会什么时候开票？", ["周杰伦"]),
    ("nb25", "offtopic", "怎么做PPT的平滑切换动画？", ["平滑切换", "PPT动画"]),
]


def norm(text):
    return re.sub(r"\s+", "", text or "")


# Post-live manual review (2026-11-07): Top1 read by hand on the live API.
REVIEW_V2 = {
    "na15": ("answerable", "2019年上半年四六级报名通知 p2 明确：四级15元/人，六级17元/人，CET-SET 50元/人次；探测词零命中但问点已可答"),
}


def main():
    hay = []
    with (ROOT / "data-v3/chunks.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            c = json.loads(line)
            if c.get("status") == "active":
                hay.append(norm(c["text"]) + "\n" + norm(c.get("doc_title", "")))

    existing_queries = set()
    for rel in ("eval/heldout/dataset_heldout_v1.json", "eval/heldout/dataset_noanswer_v1.json",
                "eval/dataset_v5_personas.json"):
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        existing_queries |= {c["query"] for c in data.get("no_answer", [])}

    candidates = []
    for case_id, query, probes in HELDOUT_NA:
        candidates.append((case_id, "heldout", query, probes))
    candidates.extend(V1_CASES)
    candidates.extend(NEW_CASES)

    cases, partial, dropped = [], [], []
    seen = set()
    for case_id, category, query, probes in candidates:
        found = {p: sum(1 for h in hay if norm(p) in h) for p in probes}
        found = {p: n for p, n in found.items() if n}
        if found:
            print("PROBE-HIT", case_id, query, found)
            dropped.append({"id": case_id, "query": query, "reason": "probe-hit", "hits": found})
            continue
        if query in seen:
            continue
        seen.add(query)
        verdict, reason = REVIEW.get(case_id, (None, None))
        verdict, reason = REVIEW_V2.get(case_id, (verdict, reason))
        if verdict == "answerable":
            print("ANSWERABLE(v1 review)", case_id, query)
            dropped.append({"id": case_id, "query": query, "reason": "v1-answerable"})
            continue
        case = {"id": case_id, "category": category, "query": query, "probes": probes}
        if verdict == "partial":
            partial.append({**case, "review": reason})
        else:
            cases.append(case)

    dataset = {
        "meta": {
            "name": "CUC KB no-answer v2 (official-site corpus)",
            "created": "2026-11-07",
            "corpus": "data-v3 (cuc_kb_v3, 2247 docs / 49571 chunks)",
            "note": "每题探测词在 data-v3 active 语料（正文+标题）零命中；v1 旧题重新校验，命中新语料的剔除；旧 partial 判定沿用；高分题 Top1 人工复核在 live 评测后进行",
        },
        "no_answer": cases,
        "partial": partial,
        "dropped": dropped,
    }
    out = Path(__file__).with_name("dataset_noanswer_v2.json")
    out.write_text(json.dumps(dataset, ensure_ascii=False, indent=1), encoding="utf-8")
    by_cat = {}
    for c in cases:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
    print(f"no_answer={len(cases)} partial={len(partial)} dropped={len(dropped)} by_category={by_cat} -> {out}")


if __name__ == "__main__":
    main()