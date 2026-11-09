#!/usr/bin/env python3
"""Dump readable excerpts of candidate docs from data-v3 for question authoring.

Usage: python eval/heldout/peek_docs.py <out.txt> <doc_id> [doc_id ...]
Full text for docs <= 2600 chars; otherwise head + lines containing digits/phones.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    out_path = Path(sys.argv[1])
    ids = sys.argv[2:]
    chunks = {}
    with (ROOT / "data-v3/chunks.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            c = json.loads(line)
            if c["doc_id"] in ids and c.get("status") == "active":
                chunks.setdefault(c["doc_id"], []).append(c)
    docs = {d["doc_id"]: d for d in json.loads((ROOT / "data-v3/docs.json").read_text(encoding="utf-8"))}
    parts = []
    for doc_id in ids:
        d = docs.get(doc_id)
        cs = sorted(chunks.get(doc_id, []), key=lambda c: c["chunk_id"])
        if not d or not cs:
            parts.append(f"### MISSING {doc_id}")
            continue
        text = "\n".join(c["text"] for c in cs)
        head = f"### {doc_id} | {d['title']} | {d.get('doc_no','')} | {d.get('department','')} | {'/'.join(d['assistants'])} | pages={d.get('pages')}"
        if len(text) <= 2600:
            body = text
        else:
            num_lines = [ln for ln in text.splitlines() if re.search(r"\d", ln) and len(ln.strip()) > 6]
            body = text[:1500] + "\n...[numeric lines]...\n" + "\n".join(num_lines[:90])
        pages = " | ".join(f"{c['chunk_id'][-4:]}:p{c['page_start']}-{c['page_end']}" for c in cs[:12])
        parts.append(head + "\n[ chunks " + pages + " ]\n" + body)
    out_path.write_text("\n\n".join(parts), encoding="utf-8")
    print(f"{len(ids)} docs -> {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()