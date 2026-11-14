#!/usr/bin/env python3
"""Build the third blind evidence-level eval set (dataset_evidence_v3.json).

Large comprehensive successor to evidence_v1 (39) / v2 (40): ~200+ fresh
questions across 12 categories and all 25 assistant domains. Cases are authored
in per-batch drafts (_v3_draft_*.json) and merged here. The builder refuses to
write the dataset unless EVERY check passes:

  * query unique vs all prior datasets (834+) and within v3
  * answerable: every evidence term verbatim in its active chunk (doc_id match),
    evidence chunks never reused from v1/v2, answer + required_points present
  * category structure rules (cross_clause same-doc multi-chunk, cross_doc
    multi-doc, multi_point single-chunk >=3 terms, table chunks is_table,
    split_adjacent consecutive same-clause, version_superseded watch docs
    carry superseded_by -> evidence doc, version_conflict watch docs unmarked)
  * unanswerable: no evidence; probes ZERO active-corpus hits; attribute_trap /
    partial must prove topic presence (topic_terms / topic_doc); personal_status
    query must be first-person

  python eval/heldout/build_evidence_v3.py            # merge all drafts
  python eval/heldout/_v3_check_draft.py <draft.json> # single-draft pre-check
"""

import hashlib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = Path(os.getenv("KB_DATA_DIR", ROOT / "data-live"))
HERE = Path(__file__).resolve().parent
OUT = HERE / "dataset_evidence_v3.json"

CATEGORIES = (
    "cross_clause", "cross_doc", "multi_point", "table", "split_adjacent",
    "version_superseded", "version_conflict", "short_query",
    "attribute_trap", "no_answer", "partial", "personal_status",
)
ANSWERABLE = set(CATEGORIES[:8])
UNANSWERABLE = set(CATEGORIES[8:])


def norm(text):
    return re.sub(r"\s+", "", text or "")


def load_corpus():
    chunks = {}
    active_texts = []
    with (DATA / "chunks.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            c = json.loads(line)
            chunks[c["chunk_id"]] = c
            if c.get("status") == "active":
                active_texts.append(norm(c.get("text", "")))
    docs = {d["doc_id"]: d for d in json.loads((DATA / "docs.json").read_text(encoding="utf-8"))}
    return chunks, "\n".join(active_texts), docs


def projection_md5():
    """Field-level hash identical to the Phoenix live projection check."""
    h = hashlib.md5()
    with (DATA / "chunks.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            c = json.loads(line)
            row = "\t".join([
                c["chunk_id"], c["doc_id"], c.get("status", ""),
                hashlib.md5(c.get("text", "").encode()).hexdigest(),
                hashlib.md5(c.get("embed_text", "").encode()).hexdigest(),
                str(c.get("superseded_by", "")),
                "|".join(sorted(c.get("assistants", []))),
            ])
            h.update((row + "\n").encode())
    return h.hexdigest()


def existing_queries():
    queries = set()
    for pattern in ("eval/dataset*.json", "eval/heldout/dataset*.json", "eval/v2/dataset*.json"):
        for path in ROOT.glob(pattern):
            if path.name == OUT.name:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            def walk(node):
                if isinstance(node, dict):
                    if isinstance(node.get("query"), str):
                        queries.add(norm(node["query"]))
                    for value in node.values():
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)

            walk(data)
    return queries


def prior_evidence_chunks():
    used = set()
    for name in ("dataset_evidence_v1.json", "dataset_evidence_v2.json"):
        path = HERE / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for case in data.get("cases", []):
            for item in case.get("evidence", []):
                if item.get("chunk_id"):
                    used.add(item["chunk_id"])
    return used


class Ctx:
    def __init__(self):
        self.chunks, self.blob, self.docs = load_corpus()
        self.blacklist = existing_queries()
        self.used_chunks = prior_evidence_chunks()
        self.seen_ids = set()
        self.seen_queries = set()


def validate_case(case, ctx):
    err = []
    cid = case.get("id", "?")
    cat = case.get("category")
    if cid in ctx.seen_ids:
        err.append(f"{cid}: duplicate id")
    ctx.seen_ids.add(cid)
    if cat not in CATEGORIES:
        err.append(f"{cid}: unknown category {cat!r}")
        return err
    if not isinstance(case.get("domain"), str) or not case["domain"]:
        err.append(f"{cid}: missing domain tag")
    q = case.get("query")
    if not isinstance(q, str) or not norm(q):
        err.append(f"{cid}: empty query")
        return err
    nq = norm(q)
    if nq in ctx.blacklist:
        err.append(f"{cid}: query duplicates a prior dataset query")
    if nq in ctx.seen_queries:
        err.append(f"{cid}: query duplicates another v3 case")
    ctx.seen_queries.add(nq)

    answerable = case.get("answerable", True)
    if cat in ANSWERABLE and not answerable:
        err.append(f"{cid}: category {cat} must be answerable")
    if cat in UNANSWERABLE and answerable:
        err.append(f"{cid}: category {cat} must set answerable=false")

    if answerable:
        if case.get("probes"):
            err.append(f"{cid}: answerable case must not carry probes")
        answer = case.get("answer", "")
        if len(norm(answer)) < 15:
            err.append(f"{cid}: answer missing/too short")
        points = case.get("required_points") or []
        min_points = 3 if cat == "multi_point" else (1 if cat in ("version_superseded", "version_conflict") else 2)
        if len(points) < min_points:
            err.append(f"{cid}: needs >= {min_points} required_points")
        evidence = case.get("evidence") or []
        if not evidence:
            err.append(f"{cid}: answerable case has no evidence")
        for item in evidence:
            chunk = ctx.chunks.get(item.get("chunk_id", ""))
            if chunk is None:
                err.append(f"{cid}: missing chunk {item.get('chunk_id')}")
                continue
            if chunk.get("status") != "active":
                err.append(f"{cid}: chunk {item['chunk_id']} not active")
            if chunk["doc_id"] != item.get("doc_id"):
                err.append(f"{cid}: doc_id mismatch for {item['chunk_id']}")
            if item["chunk_id"] in ctx.used_chunks:
                err.append(f"{cid}: evidence chunk reused from v1/v2: {item['chunk_id']}")
            text = norm(chunk.get("text", ""))
            terms = item.get("terms") or []
            if not terms:
                err.append(f"{cid}: evidence item without terms: {item['chunk_id']}")
            for term in terms:
                if len(norm(term)) < 2:
                    err.append(f"{cid}: term too short (<2 chars): {term!r}")
                elif norm(term) not in text:
                    err.append(f"{cid}: term not in {item['chunk_id']}: {term!r}")
        # category structure
        doc_ids = {i.get("doc_id") for i in evidence}
        chunk_ids = [i.get("chunk_id") for i in evidence]
        if cat == "cross_clause":
            if len(evidence) < 2 or len(set(chunk_ids)) < 2:
                err.append(f"{cid}: cross_clause needs >=2 distinct evidence chunks")
            if len(doc_ids) != 1:
                err.append(f"{cid}: cross_clause must stay in one doc (use cross_doc otherwise)")
        elif cat == "cross_doc":
            if len(doc_ids) < 2:
                err.append(f"{cid}: cross_doc needs evidence from >=2 docs")
        elif cat == "multi_point":
            if len(evidence) != 1:
                err.append(f"{cid}: multi_point needs exactly 1 evidence chunk")
            elif len(evidence[0].get("terms") or []) < 3:
                err.append(f"{cid}: multi_point chunk needs >=3 terms")
        elif cat == "table":
            if not evidence:
                err.append(f"{cid}: table needs evidence")
            for item in evidence:
                chunk = ctx.chunks.get(item.get("chunk_id", ""))
                if chunk is not None and not chunk.get("is_table"):
                    err.append(f"{cid}: table evidence chunk not is_table: {item['chunk_id']}")
        elif cat == "split_adjacent":
            if len(evidence) != 2:
                err.append(f"{cid}: split_adjacent needs exactly 2 evidence items")
            else:
                a = ctx.chunks.get(chunk_ids[0])
                b = ctx.chunks.get(chunk_ids[1])
                if a and b:
                    sa = int(chunk_ids[0].rsplit("-", 1)[1])
                    sb = int(chunk_ids[1].rsplit("-", 1)[1])
                    if sb != sa + 1 or a["doc_id"] != b["doc_id"]:
                        err.append(f"{cid}: split chunks not consecutive same-doc")
                    if a.get("clause_no") != b.get("clause_no") or not a.get("clause_no"):
                        err.append(f"{cid}: split chunks not the same clause")
        elif cat == "version_superseded":
            if not case.get("watch_docs"):
                err.append(f"{cid}: version case needs watch_docs (old version)")
            if not evidence:
                err.append(f"{cid}: version case needs evidence")
            else:
                new_doc = evidence[0]["doc_id"]
                for old in case.get("watch_docs", []):
                    old_chunks = [c for c in ctx.chunks.values() if c["doc_id"] == old]
                    if not old_chunks:
                        err.append(f"{cid}: watch doc missing {old}")
                    elif not all(c.get("superseded_by") == new_doc for c in old_chunks):
                        err.append(f"{cid}: watch doc {old} not superseded_by {new_doc}")
        elif cat == "version_conflict":
            if not case.get("watch_docs"):
                err.append(f"{cid}: version_conflict needs watch_docs (competing version)")
            if not norm(case.get("note", "")):
                err.append(f"{cid}: version_conflict needs note explaining which version is current")
            for old in case.get("watch_docs", []):
                old_chunks = [c for c in ctx.chunks.values() if c["doc_id"] == old and c.get("status") == "active"]
                if not old_chunks:
                    err.append(f"{cid}: watch doc missing/inactive {old}")
                elif any(c.get("superseded_by") for c in old_chunks):
                    err.append(f"{cid}: watch doc {old} is marked superseded (use version_superseded)")
        elif cat == "short_query":
            if len(nq) > 8:
                err.append(f"{cid}: short_query longer than 8 chars: {len(nq)}")
    else:
        if case.get("evidence"):
            err.append(f"{cid}: unanswerable case must not carry evidence")
        if case.get("answer") or case.get("required_points"):
            err.append(f"{cid}: unanswerable case must not carry answer/required_points")
        probes = case.get("probes") or []
        if len(probes) < 2 and cat != "personal_status":
            err.append(f"{cid}: needs >=2 probes")
        for probe in probes:
            if norm(probe) in ctx.blob:
                err.append(f"{cid}: probe has corpus hits: {probe!r}")
        if cat in ("attribute_trap", "partial"):
            topic_terms = case.get("topic_terms") or []
            topic_doc = case.get("topic_doc")
            topic_ok = False
            for t in topic_terms:
                if norm(t) in ctx.blob:
                    topic_ok = True
                else:
                    err.append(f"{cid}: topic_term has no corpus hits: {t!r}")
            if topic_doc:
                if not any(c["doc_id"] == topic_doc and c.get("status") == "active" for c in ctx.chunks.values()):
                    err.append(f"{cid}: topic_doc missing/inactive: {topic_doc}")
                else:
                    topic_ok = True
            if not topic_ok:
                err.append(f"{cid}: {cat} must prove topic presence via topic_terms/topic_doc")
        if cat == "personal_status" and not re.search(r"我|本人|自己", q):
            err.append(f"{cid}: personal_status query must be first-person")
    return err


def load_drafts():
    cases = []
    for path in sorted(HERE.glob("_v3_draft_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        batch_cases = data.get("cases", [])
        for case in batch_cases:
            case.setdefault("batch", data.get("batch", path.stem))
        cases.extend(batch_cases)
    return cases


def coverage(cases, docs):
    assistants = set()
    for d in docs.values():
        assistants.update(d.get("assistants") or [])
    covered = {a: [] for a in sorted(assistants)}
    for case in cases:
        if not case.get("answerable", True):
            continue
        for item in case.get("evidence", []):
            for a in docs.get(item["doc_id"], {}).get("assistants", []):
                if case["id"] not in covered[a]:
                    covered[a].append(case["id"])
    return covered


def main():
    ctx = Ctx()
    cases = load_drafts()
    if not cases:
        sys.exit("no _v3_draft_*.json files found")
    errors = []
    for case in cases:
        errors.extend(validate_case(case, ctx))
    if errors:
        print(f"VALIDATION FAILED ({len(errors)} problems):")
        for e in errors:
            print(" -", e)
        sys.exit(1)

    counts = {}
    for case in cases:
        counts[case["category"]] = counts.get(case["category"], 0) + 1
    covered = coverage(cases, ctx.docs)
    gaps = [a for a, ids in covered.items() if not ids]
    reuse = {}
    for case in cases:
        for item in case.get("evidence", []):
            reuse[item["chunk_id"]] = reuse.get(item["chunk_id"], 0) + 1
    shared = {k: v for k, v in reuse.items() if v > 1}

    md5 = hashlib.md5((DATA / "chunks.jsonl").read_bytes()).hexdigest()
    dataset = {
        "meta": {
            "name": "evidence_v3",
            "built_at": date.today().isoformat(),
            "corpus_dir": DATA.name,
            "corpus_chunks_md5": md5,
            "corpus_projection_md5": projection_md5(),
            "purpose": "blind evidence-level set #3; large comprehensive coverage "
                       "(12 categories, all 25 assistant domains); not used for tuning",
            "counts": counts,
            "answerable": sum(c.get("answerable", True) for c in cases),
            "unanswerable": sum(not c.get("answerable", True) for c in cases),
            "domain_coverage": {a: len(ids) for a, ids in covered.items()},
            "coverage_gaps": gaps,
            "shared_evidence_chunks_within_v3": len(shared),
        },
        "cases": cases,
    }
    OUT.write_text(json.dumps(dataset, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"validated {len(cases)} cases, counts={json.dumps(counts, ensure_ascii=False)}")
    print(f"answerable={dataset['meta']['answerable']} unanswerable={dataset['meta']['unanswerable']}")
    print(f"coverage gaps: {gaps or 'none'}")
    print(f"shared evidence chunks within v3: {len(shared)}")
    print(f"corpus {DATA.name} md5={md5} projection={dataset['meta']['corpus_projection_md5']}")
    print(f"saved {OUT}")
    if gaps:
        sys.exit(2)


if __name__ == "__main__":
    main()