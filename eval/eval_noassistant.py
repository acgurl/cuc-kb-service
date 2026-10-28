#!/usr/bin/env python3
import argparse
import json
import os
import re
import statistics
import time
import urllib.request
from pathlib import Path

GENERIC_TITLES = (
    "参考：教师手册",
    "学校部门分类",
    "学生工作规章制度汇编2026",
    "2026年迎接新生开学相关工作",
    "修学指南2025版",
)


def norm(text):
    return re.sub(r"\s+", "", text or "")


def request_json(api, token, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        api.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def percentile(values, pct):
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]


def doc_rank(results, expected):
    for rank, item in enumerate(results, 1):
        title = item.get("citation", {}).get("doc_title", "")
        if any(e in title for e in expected):
            return rank
    return None


def keyword_hit(results, keywords, top=5):
    texts = [norm(item.get("text", "")) for item in results[:top]]
    return any(norm(k) in text for k in keywords for text in texts)


def page_ok(results, expected, page):
    for item in results[:5]:
        title = item.get("citation", {}).get("doc_title", "")
        if not any(e in title for e in expected):
            continue
        start = int(item["citation"].get("page_start") or 0)
        end = int(item["citation"].get("page_end") or start)
        if start and start <= page <= end:
            return True
    return False


def top_generic(results):
    if not results:
        return False
    title = results[0].get("citation", {}).get("doc_title", "")
    return any(g in title for g in GENERIC_TITLES)


def top_in_domain(results, doc_meta, assistant):
    if not results or not assistant:
        return False
    assistants = doc_meta.get(results[0].get("doc_id"), {}).get("assistants", [])
    return assistant in assistants or "shared" in assistants


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default=os.getenv("KB_API_TOKEN", ""))
    parser.add_argument("--data", default="eval/dataset_v4_noassistant.json")
    parser.add_argument("--docs", default="data/docs.json")
    parser.add_argument("--out", default="/tmp/eval_v4")
    parser.add_argument("--tag", default="noassistant")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("KB_API_TOKEN is required")
    dataset = json.loads(Path(args.data).read_text(encoding="utf-8"))
    doc_meta = {d["doc_id"]: d for d in json.loads(Path(args.docs).read_text(encoding="utf-8"))}
    doc_meta["qa-csv"] = {"assistants": ["中传教务君", "问道中传"]}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    def call(payload):
        return request_json(args.api, args.token, "/v1/search", payload)

    details = {"curated": [], "short": [], "no_answer": [], "citation": []}
    lat_noassist = []
    lat_assist = []
    top1_domain = 0
    top1_domain_total = 0
    top1_generic = 0

    def run_pair(case, section):
        nonlocal top1_domain, top1_domain_total, top1_generic
        no = call({"query": case["query"], "top_k": args.top_k})
        lat_noassist.append(no["took_ms"])
        results_no = no["results"]
        rank_no = doc_rank(results_no, case.get("expected_docs", []))
        kw_no = keyword_hit(results_no, case.get("keywords", []))
        assistant = case.get("intended_assistant")
        assisted = None
        rank_as = None
        kw_as = None
        if assistant:
            assisted = call({"query": case["query"], "assistant": assistant, "top_k": args.top_k})
            lat_assist.append(assisted["took_ms"])
            results_as = assisted["results"]
            rank_as = doc_rank(results_as, case.get("expected_docs", []))
            kw_as = keyword_hit(results_as, case.get("keywords", []))
            top1_domain_total += 1
            top1_domain += 1 if top_in_domain(results_no, doc_meta, assistant) else 0
            top1_generic += 1 if top_generic(results_no) else 0
        entry = {
            "id": case["id"], "dept": case.get("dept", ""), "query": case["query"],
            "expected_docs": case.get("expected_docs", []),
            "intended_assistant": assistant,
            "no_rank": rank_no, "no_keyword": kw_no,
            "no_answer": no["no_answer"], "no_confidence": no["confidence"],
            "no_top": results_no[0]["citation"]["doc_title"] if results_no else None,
            "no_top_doc_id": results_no[0].get("doc_id") if results_no else None,
            "assist_rank": rank_as, "assist_keyword": kw_as,
            "assist_no_answer": assisted["no_answer"] if assisted else None,
            "assist_top": assisted["results"][0]["citation"]["doc_title"] if assisted and assisted["results"] else None,
            "took_ms": no["took_ms"],
            "assist_took_ms": assisted["took_ms"] if assisted else None,
            "top_generic": top_generic(results_no),
        }
        details[section].append(entry)
        return entry

    for case in dataset.get("curated", []):
        run_pair(case, "curated")
    for case in dataset.get("short", []):
        run_pair(case, "short")

    for case in dataset.get("no_answer", []):
        response = call({"query": case["query"], "top_k": args.top_k})
        lat_noassist.append(response["took_ms"])
        results = response["results"]
        details["no_answer"].append(
            {
                "id": case["id"], "query": case["query"],
                "passed": bool(response["no_answer"]),
                "no_answer": response["no_answer"], "confidence": response["confidence"],
                "top": results[0]["citation"]["doc_title"] if results else None,
                "took_ms": response["took_ms"],
            }
        )

    for case in dataset.get("citation", []):
        entry = run_pair(case, "citation")
        entry["expected_page"] = case["expected_page"]
        entry["no_page_ok"] = page_ok(call({"query": case["query"], "top_k": args.top_k})["results"], case["expected_docs"], case["expected_page"])
        if case.get("intended_assistant"):
            assisted = call(
                {"query": case["query"], "assistant": case["intended_assistant"], "top_k": args.top_k}
            )
            entry["assist_page_ok"] = page_ok(assisted["results"], case["expected_docs"], case["expected_page"])
        else:
            entry["assist_page_ok"] = None

    def hit_counts(rows, key, ks=(1, 3, 5)):
        return {k: sum(1 for r in rows if r[key] and r[key] <= k) for k in ks}

    curated = details["curated"]
    short = details["short"]
    no = details["no_answer"]
    cit = details["citation"]
    no_cur_hits = hit_counts(curated, "no_rank")
    as_cur_hits = hit_counts(curated, "assist_rank")
    no_short_hits = hit_counts(short, "no_rank")
    as_short_hits = hit_counts(short, "assist_rank")
    no_cur_mrr = sum(1.0 / r["no_rank"] for r in curated if r["no_rank"]) / max(len(curated), 1)
    as_cur_mrr = sum(1.0 / r["assist_rank"] for r in curated if r["assist_rank"]) / max(len(curated), 1)
    no_short_mrr = sum(1.0 / r["no_rank"] for r in short if r["no_rank"]) / max(len(short), 1)
    as_short_mrr = sum(1.0 / r["assist_rank"] for r in short if r["assist_rank"]) / max(len(short), 1)
    no_cur_ans = sum(1 for r in curated if not r["no_answer"])
    as_cur_ans = sum(1 for r in curated if r["assist_no_answer"] is not None and not r["assist_no_answer"])
    no_short_ans = sum(1 for r in short if not r["no_answer"])
    as_short_ans = sum(1 for r in short if r["assist_no_answer"] is not None and not r["assist_no_answer"])

    per_assistant = {}
    for row in curated:
        assistant = row["intended_assistant"] or "(none)"
        entry = per_assistant.setdefault(
            assistant, {"total": 0, "no_hit5": 0, "as_hit5": 0, "no_answered": 0, "as_answered": 0}
        )
        entry["total"] += 1
        entry["no_hit5"] += 1 if row["no_rank"] and row["no_rank"] <= 5 else 0
        entry["as_hit5"] += 1 if row["assist_rank"] and row["assist_rank"] <= 5 else 0
        entry["no_answered"] += 1 if not row["no_answer"] else 0
        entry["as_answered"] += 1 if row["assist_no_answer"] is not None and not row["assist_no_answer"] else 0

    summary = {
        "tag": args.tag,
        "curated_total": len(curated),
        "noassist_cur@1": no_cur_hits[1], "noassist_cur@3": no_cur_hits[3], "noassist_cur@5": no_cur_hits[5],
        "noassist_cur_kw@5": sum(1 for r in curated if r["no_keyword"]),
        "noassist_cur_mrr": round(no_cur_mrr, 4), "noassist_cur_answered": no_cur_ans,
        "assist_cur@1": as_cur_hits[1], "assist_cur@3": as_cur_hits[3], "assist_cur@5": as_cur_hits[5],
        "assist_cur_kw@5": sum(1 for r in curated if r["assist_keyword"]),
        "assist_cur_mrr": round(as_cur_mrr, 4), "assist_cur_answered": as_cur_ans,
        "delta_cur@1": as_cur_hits[1] - no_cur_hits[1],
        "delta_cur@5": as_cur_hits[5] - no_cur_hits[5],
        "delta_cur_mrr": round(as_cur_mrr - no_cur_mrr, 4),
        "short_total": len(short),
        "noassist_short@1": no_short_hits[1], "noassist_short@3": no_short_hits[3], "noassist_short@5": no_short_hits[5],
        "noassist_short_kw@5": sum(1 for r in short if r["no_keyword"]),
        "noassist_short_answered": no_short_ans, "noassist_short_mrr": round(no_short_mrr, 4),
        "assist_short@1": as_short_hits[1], "assist_short@3": as_short_hits[3], "assist_short@5": as_short_hits[5],
        "assist_short_kw@5": sum(1 for r in short if r["assist_keyword"]),
        "assist_short_answered": as_short_ans, "assist_short_mrr": round(as_short_mrr, 4),
        "no_answer_total": len(no), "no_answer_pass": sum(1 for r in no if r["passed"]),
        "citation_total": len(cit),
        "noassist_citation_pass": sum(1 for r in cit if r["no_page_ok"]),
        "assist_citation_pass": sum(1 for r in cit if r["assist_page_ok"]),
        "top1_in_intended_domain": top1_domain, "top1_domain_total": top1_domain_total,
        "top1_generic": top1_generic,
        "latency_noassist_p50": percentile(lat_noassist, 50),
        "latency_noassist_p95": percentile(lat_noassist, 95),
        "latency_assist_p50": percentile(lat_assist, 50),
        "latency_assist_p95": percentile(lat_assist, 95),
        "per_assistant": per_assistant,
    }

    (out_dir / ("results_%s.json" % args.tag)).write_text(
        json.dumps({"summary": summary, "details": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# CUC KB 无指定助手评测 — %s" % args.tag,
        "",
        "测试集：%d 文档检索 + %d 短问句 + %d 无答案 + %d 页码引用；每条同时跑无 assistant 与正确 assistant 对照。"
        % (len(curated), len(short), len(no), len(cit)),
        "",
        "## 无指定助手（重点）",
        "| 指标 | 结果 |",
        "|---|---|",
        "| 文档命中@1 | %d/%d |" % (no_cur_hits[1], len(curated)),
        "| 文档命中@3 | %d/%d |" % (no_cur_hits[3], len(curated)),
        "| 文档命中@5 | %d/%d |" % (no_cur_hits[5], len(curated)),
        "| 关键词命中@5 | %d/%d |" % (summary["noassist_cur_kw@5"], len(curated)),
        "| MRR | %.4f |" % no_cur_mrr,
        "| 有效问题回答率 | %d/%d |" % (no_cur_ans, len(curated)),
        "| 短问句命中@5 | %d/%d |" % (no_short_hits[5], len(short)),
        "| 短问句回答率 | %d/%d |" % (no_short_ans, len(short)),
        "| 无答案准确率 | %d/%d |" % (summary["no_answer_pass"], len(no)),
        "| 页码引用正确 | %d/%d |" % (summary["noassist_citation_pass"], len(cit)),
        "| Top1 落在预期助手域 | %d/%d |" % (top1_domain, top1_domain_total),
        "| Top1 是通用大文档 | %d |" % top1_generic,
        "| P50 / P95 | %s / %s ms |" % (summary["latency_noassist_p50"], summary["latency_noassist_p95"]),
        "",
        "## 指定正确助手（对照）",
        "| 指标 | 结果 | 与无助手差异 |",
        "|---|---|---|",
        "| 文档命中@1 | %d/%d | %+d |" % (as_cur_hits[1], len(curated), as_cur_hits[1] - no_cur_hits[1]),
        "| 文档命中@5 | %d/%d | %+d |" % (as_cur_hits[5], len(curated), as_cur_hits[5] - no_cur_hits[5]),
        "| 关键词命中@5 | %d/%d | %+d |" % (summary["assist_cur_kw@5"], len(curated), summary["assist_cur_kw@5"] - summary["noassist_cur_kw@5"]),
        "| MRR | %.4f | %+.4f |" % (as_cur_mrr, as_cur_mrr - no_cur_mrr),
        "| 短问句命中@5 | %d/%d | %+d |" % (as_short_hits[5], len(short), as_short_hits[5] - no_short_hits[5]),
        "| 页码引用正确 | %d/%d | %+d |" % (summary["assist_citation_pass"], len(cit), summary["assist_citation_pass"] - summary["noassist_citation_pass"]),
        "| P50 / P95 | %s / %s ms | |" % (summary["latency_assist_p50"], summary["latency_assist_p95"]),
        "",
        "## 分助手（无助手 → 指定助手）",
        "| 助手 | 题数 | 命中@5 | 回答数 |",
        "|---|---|---|---|",
    ]
    for assistant, entry in sorted(per_assistant.items(), key=lambda x: (-x[1]["total"], x[0])):
        lines.append(
            "| %s | %d | %d/%d → %d/%d | %d/%d → %d/%d |"
            % (
                assistant, entry["total"], entry["no_hit5"], entry["total"], entry["as_hit5"], entry["total"],
                entry["no_answered"], entry["total"], entry["as_answered"], entry["total"],
            )
        )
    lines += ["", "## Bad cases（无助手）"]
    for row in curated:
        if not row["no_rank"] or row["no_rank"] > 5 or not row["no_keyword"] or row["no_answer"]:
            lines.append(
                "- [%s][%s] %s | rank=%s | kw=%s | answer=%s | top=%s"
                % (row["id"], row["dept"], row["query"], row["no_rank"], row["no_keyword"], not row["no_answer"], row["no_top"])
            )
    for row in short:
        if row["no_answer"] or not row["no_rank"] or row["no_rank"] > 5:
            lines.append(
                "- [%s][短] %s | rank=%s | answer=%s | top=%s"
                % (row["id"], row["query"], row["no_rank"], not row["no_answer"], row["no_top"])
            )
    for row in no:
        if not row["passed"]:
            lines.append("- [%s][无答案] %s | conf=%s | top=%s" % (row["id"], row["query"], row["confidence"], row["top"]))
    for row in cit:
        if not row["no_page_ok"]:
            lines.append("- [%s][页码] %s | expected=%s | top=%s" % (row["id"], row["query"], row["expected_page"], row["no_top"]))
    lines += ["", "## 助手提示的影响"]
    for row in curated + short:
        if not row["intended_assistant"]:
            continue
        no_ok = row["no_rank"] and row["no_rank"] <= 5
        as_ok = row["assist_rank"] and row["assist_rank"] <= 5
        if as_ok and not no_ok:
            lines.append("- [%s] 助手提示修复：%s | 无=%s → 指定=%s" % (row["id"], row["query"], row["no_rank"], row["assist_rank"]))
        elif no_ok and not as_ok:
            lines.append("- [%s] 助手提示反而丢失：%s | 无=%s → 指定=%s" % (row["id"], row["query"], row["no_rank"], row["assist_rank"]))
    lines += ["", "## Top1 通用大文档（非期望文档）"]
    for row in curated:
        if row["top_generic"] and not any(e in (row["no_top"] or "") for e in row["expected_docs"]):
            lines.append("- [%s] %s | top=%s" % (row["id"], row["query"], row["no_top"]))
    lines += ["", "## Top1 不在预期助手域"]
    for row in curated + short + cit:
        if not row["intended_assistant"]:
            continue
        meta = doc_meta.get(row["no_top_doc_id"], {})
        assistants = meta.get("assistants", [])
        if row["intended_assistant"] not in assistants and "shared" not in assistants:
            lines.append(
                "- [%s] %s | 预期助手=%s | top=%s"
                % (row["id"], row["query"], row["intended_assistant"], row["no_top"])
            )
    (out_dir / ("report_%s.md" % args.tag)).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("report:", out_dir / ("report_%s.md" % args.tag))


if __name__ == "__main__":
    main()
