#!/usr/bin/env python3
"""Upload new-batch source PDFs to the private OCI bucket, keyed by doc_id.

Phoenix's instance principal has no Object Storage write permission, so this
runs from the local Windows OCI config (~/.oci/config). Only docs present in
--docs but absent from --skip-docs are uploaded (the live corpus is already in
the bucket). Objects land at {FILE_OBJECT_PREFIX}/{doc_id}.pdf to match
kb/file_access.object_name, so citation PAR redirects resolve immediately.

  python -m scripts.upload_pdfs_v3 --docs data-v3/docs.json \
      --skip-docs data/docs.json --corpus <batch>/pdf-corpus [--threads 6] [--dry-run]
"""

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import oci

from kb.common import FILE_OBJECT_PREFIX, OCI_BUCKET, OCI_NAMESPACE

_local = threading.local()


def client():
    c = getattr(_local, "client", None)
    if c is None:
        config = oci.config.from_file()
        c = oci.object_storage.ObjectStorageClient(config)
        _local.client = c
    return c


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", required=True)
    parser.add_argument("--skip-docs", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    docs = json.loads(Path(args.docs).read_text(encoding="utf-8"))
    skip = {d["doc_id"] for d in json.loads(Path(args.skip_docs).read_text(encoding="utf-8"))}
    corpus = Path(args.corpus)
    todo = []
    for doc in docs:
        if doc["doc_id"] in skip:
            continue
        rel = doc.get("source_file", "")
        path = corpus / rel
        if not path.exists():
            raise SystemExit(f"missing source PDF for {doc['doc_id']}: {path}")
        todo.append((doc["doc_id"], doc.get("title") or path.name, path))
    total_bytes = sum(p.stat().st_size for _, _, p in todo)
    print(f"{len(todo)} PDFs to upload, {total_bytes / 1e6:.1f} MB, threads={args.threads}")
    if args.dry_run:
        return

    done = 0
    failed = []
    started = time.time()
    lock = threading.Lock()

    def upload(item):
        doc_id, title, path = item
        object_name = f"{FILE_OBJECT_PREFIX}/{doc_id}.pdf" if FILE_OBJECT_PREFIX else f"{doc_id}.pdf"
        disposition = "inline; filename*=UTF-8''" + quote(path.name)
        with path.open("rb") as body:
            client().put_object(
                OCI_NAMESPACE,
                OCI_BUCKET,
                object_name,
                body,
                content_type="application/pdf",
                content_disposition=disposition,
            )
        return doc_id, path.stat().st_size

    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futures = {pool.submit(upload, item): item[0] for item in todo}
        for future in as_completed(futures):
            doc_id = futures[future]
            try:
                future.result()
                with lock:
                    done += 1
                    if done % 100 == 0 or done == len(todo):
                        print(f"{done}/{len(todo)} uploaded ({time.time() - started:.0f}s)", flush=True)
            except Exception as exc:  # noqa: BLE001
                failed.append((doc_id, str(exc)))
                print(f"FAIL {doc_id}: {exc}", flush=True)

    print(f"done: {done} ok, {len(failed)} failed in {time.time() - started:.0f}s")
    if failed:
        Path("upload_pdfs_v3_failed.json").write_text(
            json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise SystemExit("some uploads failed; see upload_pdfs_v3_failed.json")


if __name__ == "__main__":
    main()