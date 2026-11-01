#!/usr/bin/env python3
"""v1 eval (eval/dataset.json). Its percentile/keyword_hit differ from kbeval on
purpose so that report.md stays reproducible."""
import argparse
import csv
import json
import os
import time
from pathlib import Path

from kbeval import DEFAULT_API, request_json


def post(api_url, token, payload):
    return request_json(api_url, token, "/v1/search", payload, timeout=60)


def percentile(values, pct):
    if not values:
        return 0
    ordered = sorted(values)
    index = min(int(len(ordered) * pct / 100), len(ordered) - 1)
    return ordered[index]


def contains_any(text, needles):
    return any(needle and needle in text for needle in needles)


def doc_rank(results, expected_docs):
    for rank, item in enumerate(results, 1):
        title = item["citation"]["doc_title"]
        if contains_any(title, expected_docs):
            return rank
    return None


def keyword_hit(results, keywords):
    combined = ""
    for item in results:
        citation = item["citation"]
        combined += item["text"]
        combined += citation.get("doc_title", "")
        combined += " ".join(citation.get("section_path") or [])
    return any(keyword in combined for keyword in keywords)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--token", default=os.getenv("KB_API_TOKEN", ""))
    parser.add_argument("--data", default="eval/dataset.json")
    parser.add_argument("--qa", default="QA知识导入模板.csv")
    parser.add_argument("--out", default="eval")
    parser.add_argument(
        "--rerank-pause",
        type=float,
        default=0.0,
        help="seconds to sleep after a query that used rerank (rate limit friendly)",
    )
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("KB_API_TOKEN is required")
    dataset = json.loads(Path(args.data).read_text(encoding="utf-8"))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = request_json(args.api, args.token, "/v1/meta")

    def call(payload):
        response = post(args.api, args.token, payload)
        if args.rerank_pause and any(
            item.get("rerank_score") is not None for item in response["results"]
        ):
            time.sleep(args.rerank_pause)
        return response

    details = {"qa": [], "curated": [], "short": [], "no_answer": [], "isolation": []}
    latencies = []

    qa_rows = []
    with Path(args.qa).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            question = (row.get("问题") or "").strip()
            if question:
                qa_rows.append(question)

    qa_top1 = qa_top3 = 0
    for index, question in enumerate(qa_rows, 1):
        response = call({"query": question, "top_k": 5})
        results = response["results"]
        latencies.append(response["took_ms"])
        rank = next(
            (
                position
                for position, item in enumerate(results, 1)
                if item.get("is_qa") or item["citation"]["doc_title"] == "QA知识导入模板"
            ),
            None,
        )
        qa_top1 += 1 if rank == 1 else 0
        qa_top3 += 1 if rank and rank <= 3 else 0
        details["qa"].append(
            {
                "id": "qa-%02d" % index,
                "query": question,
                "rank": rank,
                "top_title": results[0]["citation"]["doc_title"] if results else None,
                "took_ms": response["took_ms"],
            }
        )

    curated_hits = {1: 0, 3: 0, 5: 0}
    keyword_hits = 0
    mrr = 0.0
    for case in dataset["curated"]:
        response = call({"query": case["query"], "top_k": 5})
        results = response["results"]
        latencies.append(response["took_ms"])
        rank = doc_rank(results, case["expected_docs"])
        for k in curated_hits:
            if rank and rank <= k:
                curated_hits[k] += 1
        if rank:
            mrr += 1.0 / rank
        hit_keyword = keyword_hit(results, case["keywords"])
        keyword_hits += 1 if hit_keyword else 0
        details["curated"].append(
            {
                "id": case["id"],
                "dept": case["dept"],
                "query": case["query"],
                "expected_docs": case["expected_docs"],
                "doc_rank": rank,
                "keyword_hit": hit_keyword,
                "no_answer": response["no_answer"],
                "top_title": results[0]["citation"]["doc_title"] if results else None,
                "top_text": results[0]["text"][:180] if results else "",
                "took_ms": response["took_ms"],
            }
        )

    short_cases = dataset.get("short", [])
    short_hits = {1: 0, 3: 0, 5: 0}
    short_keyword_hits = 0
    short_valid_total = 0
    short_valid_answered = 0
    short_na_total = 0
    short_na_pass = 0
    short_mrr = 0.0
    for case in short_cases:
        response = call({"query": case["query"], "top_k": 5})
        results = response["results"]
        latencies.append(response["took_ms"])
        if case.get("expect_no_answer"):
            short_na_total += 1
            passed = bool(response["no_answer"])
            short_na_pass += 1 if passed else 0
            details["short"].append(
                {
                    "id": case["id"],
                    "dept": case["dept"],
                    "query": case["query"],
                    "expect_no_answer": True,
                    "passed": passed,
                    "no_answer": response["no_answer"],
                    "top_title": results[0]["citation"]["doc_title"] if results else None,
                    "took_ms": response["took_ms"],
                }
            )
            continue

        short_valid_total += 1
        answered = not response["no_answer"]
        short_valid_answered += 1 if answered else 0
        rank = doc_rank(results, case["expected_docs"])
        for k in short_hits:
            if rank and rank <= k:
                short_hits[k] += 1
        if rank:
            short_mrr += 1.0 / rank
        hit_keyword = keyword_hit(results, case["keywords"])
        short_keyword_hits += 1 if hit_keyword else 0
        details["short"].append(
            {
                "id": case["id"],
                "dept": case["dept"],
                "query": case["query"],
                "expect_no_answer": False,
                "answered": answered,
                "doc_rank": rank,
                "keyword_hit": hit_keyword,
                "no_answer": response["no_answer"],
                "top_title": results[0]["citation"]["doc_title"] if results else None,
                "top_text": results[0]["text"][:180] if results else "",
                "took_ms": response["took_ms"],
            }
        )

    no_answer_pass = 0
    for case in dataset["no_answer"]:
        response = call({"query": case["query"], "top_k": 5})
        latencies.append(response["took_ms"])
        passed = bool(response["no_answer"])
        no_answer_pass += 1 if passed else 0
        details["no_answer"].append(
            {
                "id": case["id"],
                "query": case["query"],
                "passed": passed,
                "confidence": response["confidence"],
                "top_title": response["results"][0]["citation"]["doc_title"]
                if response["results"]
                else None,
                "took_ms": response["took_ms"],
            }
        )

    isolation_pass = 0
    for case in dataset["isolation"]:
        response = call(
            {"query": case["query"], "assistant": case["assistant"], "top_k": 5}
        )
        results = response["results"]
        latencies.append(response["took_ms"])
        titles = [item["citation"]["doc_title"] for item in results]
        if case["positive"]:
            passed = (not response["no_answer"]) and contains_any(
                " ".join(titles), case["expected_docs"]
            )
        else:
            passed = not contains_any(" ".join(titles), case["forbidden_docs"])
        isolation_pass += 1 if passed else 0
        details["isolation"].append(
            {
                "id": case["id"],
                "query": case["query"],
                "assistant": case["assistant"],
                "positive": case["positive"],
                "passed": passed,
                "no_answer": response["no_answer"],
                "titles": titles,
                "took_ms": response["took_ms"],
            }
        )

    summary = {
        "qa_total": len(qa_rows),
        "qa_top1": qa_top1,
        "qa_top3": qa_top3,
        "curated_total": len(dataset["curated"]),
        "curated_hit1": curated_hits[1],
        "curated_hit3": curated_hits[3],
        "curated_hit5": curated_hits[5],
        "curated_keyword5": keyword_hits,
        "curated_mrr": round(mrr / max(len(dataset["curated"]), 1), 4),
        "short_total": len(short_cases),
        "short_valid_total": short_valid_total,
        "short_valid_answered": short_valid_answered,
        "short_hit1": short_hits[1],
        "short_hit3": short_hits[3],
        "short_hit5": short_hits[5],
        "short_keyword5": short_keyword_hits,
        "short_na_total": short_na_total,
        "short_na_pass": short_na_pass,
        "short_mrr": round(short_mrr / max(short_valid_total, 1), 4),
        "no_answer_total": len(dataset["no_answer"]),
        "no_answer_pass": no_answer_pass,
        "isolation_total": len(dataset["isolation"]),
        "isolation_pass": isolation_pass,
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "latency_max_ms": max(latencies) if latencies else 0,
    }

    report = [
        "# CUC 知识库评测报告",
        "",
        "- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "- 库：%d 个文档 / %d 个知识块" % (meta["documents"], meta["chunks"]),
        "- 测试集：%d 条 QA 精确查找 + %d 条跨部门文档检索 + %d 条短问句 + %d 条无答案 + %d 条助手隔离"
        % (
            len(qa_rows),
            len(dataset["curated"]),
            len(short_cases),
            len(dataset["no_answer"]),
            len(dataset["isolation"]),
        ),
        "- 说明：QA 精确查找验证问答表通路；文档检索以跨部门 curated 集为准；短问句单独统计。",
        "",
        "## 总览",
        "",
        "| 指标 | 结果 |",
        "|---|---|",
        "| QA 精确查找 Top1 | %d/%d (%.1f%%) |"
        % (qa_top1, len(qa_rows), 100 * qa_top1 / max(len(qa_rows), 1)),
        "| QA 精确查找 Top3 | %d/%d (%.1f%%) |"
        % (qa_top3, len(qa_rows), 100 * qa_top3 / max(len(qa_rows), 1)),
        "| 跨部门文档命中@1 | %d/%d |" % (curated_hits[1], len(dataset["curated"])),
        "| 跨部门文档命中@3 | %d/%d |" % (curated_hits[3], len(dataset["curated"])),
        "| 跨部门文档命中@5 | %d/%d |" % (curated_hits[5], len(dataset["curated"])),
        "| 关键信息命中@5 | %d/%d |"
        % (keyword_hits, len(dataset["curated"])),
        "| MRR | %.4f |" % summary["curated_mrr"],
        "| 短问句有效题回答率 | %d/%d |"
        % (short_valid_answered, short_valid_total),
        "| 短问句文档命中@5 | %d/%d |" % (short_hits[5], short_valid_total),
        "| 短问句关键词命中@5 | %d/%d |" % (short_keyword_hits, short_valid_total),
        "| 短问句无答案准确率 | %d/%d |" % (short_na_pass, short_na_total),
        "| 无答案准确率 | %d/%d |" % (no_answer_pass, len(dataset["no_answer"])),
        "| 助手隔离通过率 | %d/%d |"
        % (isolation_pass, len(dataset["isolation"])),
        "| 延迟 P50 / P95 | %d / %d ms |"
        % (summary["latency_p50_ms"], summary["latency_p95_ms"]),
        "",
        "## Bad cases",
        "",
    ]

    bad_curated = [
        item
        for item in details["curated"]
        if item["doc_rank"] is None or not item["keyword_hit"]
    ]
    for item in bad_curated:
        report.append(
            "- [%s][%s] %s | doc_rank=%s | keyword=%s | top=%s"
            % (
                item["id"],
                item["dept"],
                item["query"],
                item["doc_rank"],
                item["keyword_hit"],
                item["top_title"],
            )
        )
    bad_short = [
        item
        for item in details["short"]
        if (
            (item["expect_no_answer"] and not item["passed"])
            or (
                not item["expect_no_answer"]
                and (
                    not item["answered"]
                    or item["doc_rank"] is None
                    or not item["keyword_hit"]
                )
            )
        )
    ]
    for item in bad_short:
        report.append(
            "- [%s][%s] %s | answered=%s | doc_rank=%s | keyword=%s | top=%s"
            % (
                item["id"],
                item["dept"],
                item["query"],
                item.get("answered"),
                item.get("doc_rank"),
                item.get("keyword_hit"),
                item["top_title"],
            )
        )
    bad_qa = [item for item in details["qa"] if item["rank"] != 1]
    for item in bad_qa:
        report.append(
            "- [%s] %s | rank=%s | top=%s"
            % (item["id"], item["query"], item["rank"], item["top_title"])
        )
    bad_no_answer = [item for item in details["no_answer"] if not item["passed"]]
    for item in bad_no_answer:
        report.append(
            "- [%s] %s | 未判无答案 | top=%s"
            % (item["id"], item["query"], item["top_title"])
        )
    bad_isolation = [item for item in details["isolation"] if not item["passed"]]
    for item in bad_isolation:
        report.append(
            "- [%s] %s | assistant=%s | titles=%s"
            % (item["id"], item["query"], item["assistant"], item["titles"][:3])
        )
    if not (bad_curated or bad_short or bad_qa or bad_no_answer or bad_isolation):
        report.append("- 无")

    report.extend(
        [
            "",
            "## 限制",
            "",
            "- QA 表本身已入库，精确查找 100% 只代表问答表通路可用，不代表文档检索 100%。",
            "- 跨部门集为人工构造的 %d 条问题，覆盖多个部门，规模仍偏小。"
            % len(dataset["curated"]),
            "- 只评测检索与无答案判定，未评测最终答案生成的事实一致性。",
            "- 2023 版学生工作汇编已标记 superseded，不参与默认召回。",
            "- 扫描/截图型文档未做 OCR，未纳入本次文档检索评测。",
        ]
    )

    (out_dir / "results.json").write_text(
        json.dumps({"summary": summary, "details": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(
        "bad cases:",
        len(bad_curated)
        + len(bad_short)
        + len(bad_qa)
        + len(bad_no_answer)
        + len(bad_isolation),
    )
    print("report:", out_dir / "report.md")


if __name__ == "__main__":
    main()
