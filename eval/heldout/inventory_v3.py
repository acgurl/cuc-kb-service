#!/usr/bin/env python3
"""Inventory new (2026-11-07 official-site batch) docs not referenced by any
existing eval dataset, so held-out v2 can be built from untouched documents.

Outputs eval/heldout/_inventory_v3.tsv (untracked working file).
"""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DATASETS = [
    "eval/dataset.json",
    "eval/dataset_v2.json",
    "eval/dataset_v3.json",
    "eval/dataset_v4_noassistant.json",
    "eval/dataset_v5_personas.json",
    "eval/heldout/dataset_heldout_v1.json",
]


def main():
    old = {d["doc_id"] for d in json.loads((ROOT / "data/docs.json").read_text(encoding="utf-8"))}
    docs = json.loads((ROOT / "data-v3/docs.json").read_text(encoding="utf-8"))
    added = [d for d in docs if d["doc_id"] not in old]

    referenced = set()
    for rel in DATASETS:
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        for section in ("curated", "short", "isolation", "citation"):
            for case in data.get(section, []):
                referenced.update(case.get("expected_docs", []))

    fresh, touched = [], []
    for d in added:
        title = d["title"]
        if any(e in title for e in referenced):
            touched.append(d)
        else:
            fresh.append(d)

    lines = ["doc_id\ttitle\tdoc_no\tdepartment\tassistants\tdoc_type\tpages\tchars\tis_generic"]
    for d in sorted(fresh, key=lambda x: ("/".join(x["assistants"]), x["department"], x["title"])):
        lines.append("\t".join([
            d["doc_id"], d["title"], d.get("doc_no", ""), d.get("department", ""),
            "/".join(d["assistants"]), d.get("doc_type", ""),
            str(d.get("pages", "")), str(d.get("chars", "")), str(d.get("is_generic", False)),
        ]))
    out = Path(__file__).with_name("_inventory_v3.tsv")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"added={len(added)} referenced_by_old_sets={len(touched)} fresh={len(fresh)}")
    print("fresh by assistant:", Counter(a for d in fresh for a in d["assistants"]).most_common())
    print("fresh by type:", Counter(d["doc_type"] for d in fresh))
    print("fresh with doc_no:", sum(bool(d.get("doc_no")) for d in fresh))
    print("->", out)


if __name__ == "__main__":
    main()