#!/usr/bin/env python3
"""Enrich pure-number table chunks' embed_text with their caption (item ②).

The discipline/topic caption (e.g. "表1.1 …（文学和艺术学）") lives in the text
*before* a table, so the table chunk itself lacks those recall tokens. This adds
the caption to embed_text only (text stays verbatim for evidence/citation).

  python -m scripts.enrich_table_embed --data data-live             # dry-run report
  python -m scripts.enrich_table_embed --data data-live --apply     # patch + backup

--apply rewrites embed_text in chunks.jsonl and writes _table_reembed.jsonl (only
the changed chunks) for the existing re-embed pipeline:
  scripts/batch_embed.py generate --chunks _table_reembed.jsonl ... → submit →
  fetch → scripts/load_vectors.py (upsert into Qdrant).
Re-embedding + Qdrant sync are a separate, billable step — run them deliberately.
"""

import argparse
import json
import shutil
from pathlib import Path

from kb.ingest import table_caption

ROOT = Path(__file__).resolve().parent.parent
LOOKBACK = 3  # how many preceding chunks to search for the caption


def load(data_dir):
    chunks = []
    with (data_dir / "chunks.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def caption_for(chunk, doc_chunks, index):
    # the table's own text first, then preceding chunks (newest first)
    texts = [chunk.get("text", "")]
    texts += [doc_chunks[i].get("text", "") for i in range(index - 1, max(-1, index - LOOKBACK - 1), -1)]
    for t in texts:
        cap = table_caption(t)
        if cap:
            return cap
    return ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data-live")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    data_dir = ROOT / args.data
    chunks = load(data_dir)

    doc_chunks = {}
    for c in chunks:
        doc_chunks.setdefault(c["doc_id"], []).append(c)

    changed = []
    for c in chunks:
        if not c.get("is_table") or c.get("status") != "active":
            continue
        siblings = doc_chunks[c["doc_id"]]
        pos = next(i for i, x in enumerate(siblings) if x["chunk_id"] == c["chunk_id"])
        cap = caption_for(c, siblings, pos)
        if cap and cap not in c["embed_text"]:
            changed.append((c, cap))

    print(f"active table chunks to enrich: {len(changed)}")
    for c, cap in changed[:12]:
        print(f"  {c['chunk_id']}: + {cap}")
    if not args.apply:
        print("dry-run; pass --apply to patch embed_text and emit _table_reembed.jsonl")
        return

    suffix = ".bak-tablecap"
    for name in ("chunks.jsonl",):
        path = data_dir / name
        backup = Path(str(path) + suffix)
        if not backup.exists():
            shutil.copy2(path, backup)
    changed_ids = {c["chunk_id"] for c, _ in changed}
    cap_of = {c["chunk_id"]: cap for c, cap in changed}
    for c in chunks:
        if c["chunk_id"] in changed_ids:
            cap = cap_of[c["chunk_id"]]
            # insert caption right after the 《title》 prefix line, before the table
            lines = c["embed_text"].split("\n", 1)
            c["embed_text"] = lines[0] + "\n" + cap + ("\n" + lines[1] if len(lines) > 1 else "")
    with (data_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for c in chunks:
            handle.write(json.dumps(c, ensure_ascii=False) + "\n")
    reembed = data_dir / "_table_reembed.jsonl"
    with reembed.open("w", encoding="utf-8") as handle:
        for c in chunks:
            if c["chunk_id"] in changed_ids:
                handle.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"applied to {len(changed)} chunks; backup {data_dir}/chunks.jsonl{suffix}")
    print(f"re-embed input: {reembed} (then batch_embed generate/submit/fetch + load_vectors)")


if __name__ == "__main__":
    main()