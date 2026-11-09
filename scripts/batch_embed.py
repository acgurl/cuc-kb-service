#!/usr/bin/env python3
"""Bailian Batch File API workflow for offline document embeddings.

Subcommands (run from the repo root):
  generate  build batch_input.jsonl + manifest.json from chunks.jsonl file(s)
  submit    upload the input file and create the batch job
  status    print job status (--wait polls until terminal)
  fetch     download results into vectors.npz (ids + float32 matrix)

The API key is read from EMBED_API_KEY, RERANK_API_KEY or DASHSCOPE_API_KEY.
Batch pricing is 50% of realtime; results are matched back by custom_id =
chunk_id. Load the vectors with scripts/load_vectors.py.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

MODEL_DEFAULT = "qwen3.7-text-embedding"
API_ROOT_DEFAULT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MAX_REQUESTS = 50000
MAX_FILE_BYTES = 500 * 1024 * 1024
TERMINAL = ("completed", "failed", "expired", "cancelled")


def api_key():
    key = (
        os.getenv("EMBED_API_KEY", "").strip()
        or os.getenv("RERANK_API_KEY", "").strip()
        or os.getenv("DASHSCOPE_API_KEY", "").strip()
    )
    if not key:
        sys.exit("set EMBED_API_KEY / RERANK_API_KEY / DASHSCOPE_API_KEY")
    return key


def api_root():
    return os.getenv("BATCH_API_BASE", "").strip() or API_ROOT_DEFAULT


def request(method, path, body=None, key=None, timeout=120):
    headers = {"Authorization": "Bearer " + (key or api_key())}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(api_root() + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:500]
        sys.exit(f"HTTP {exc.code} {method} {path}: {detail}")


def read_job(directory):
    path = directory / "job.json"
    if not path.exists():
        sys.exit(f"missing {path}; run submit first")
    return json.loads(path.read_text(encoding="utf-8"))


def write_job(directory, job):
    (directory / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_generate(args):
    directory = Path(args.dir)
    directory.mkdir(parents=True, exist_ok=True)
    seen = set()
    count = 0
    out_path = directory / "batch_input.jsonl"
    with out_path.open("w", encoding="utf-8") as out:
        for source in args.chunks:
            with open(source, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    chunk_id = chunk["chunk_id"]
                    if chunk_id in seen:
                        sys.exit(f"duplicate chunk_id across inputs: {chunk_id}")
                    seen.add(chunk_id)
                    text = chunk.get("embed_text") or chunk.get("text") or ""
                    if not text.strip():
                        sys.exit(f"empty embed_text: {chunk_id}")
                    req = {
                        "custom_id": chunk_id,
                        "method": "POST",
                        "url": "/v1/embeddings",
                        "body": {
                            "model": args.model,
                            "input": text,
                            "encoding_format": "float",
                            "dimensions": args.dim,
                        },
                    }
                    out.write(json.dumps(req, ensure_ascii=False) + "\n")
                    count += 1
                    if count > MAX_REQUESTS:
                        sys.exit(f"more than {MAX_REQUESTS} requests; split the input")
    size = out_path.stat().st_size
    if size > MAX_FILE_BYTES:
        sys.exit(f"input file {size} bytes exceeds {MAX_FILE_BYTES}")
    manifest = {
        "model": args.model,
        "dim": args.dim,
        "count": count,
        "bytes": size,
        "sources": [str(Path(s).resolve()) for s in args.chunks],
        "chunk_ids": sorted(seen),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    print(f"generated {out_path}: {count} requests, {size / 1e6:.1f} MB, model={args.model} dim={args.dim}")


def cmd_submit(args):
    directory = Path(args.dir)
    input_path = directory / "batch_input.jsonl"
    if not input_path.exists():
        sys.exit(f"missing {input_path}; run generate first")
    key = api_key()
    boundary = "----cucbatch" + uuid.uuid4().hex
    body = b""
    body += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nbatch\r\n"
    ).encode()
    body += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{input_path.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
    ).encode() + input_path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        api_root() + "/files",
        data=body,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        },
    )
    print(f"uploading {input_path.stat().st_size / 1e6:.1f} MB ...", flush=True)
    with urllib.request.urlopen(req, timeout=600) as response:
        uploaded = json.loads(response.read().decode("utf-8"))
    print(f"file_id={uploaded['id']} status={uploaded.get('status')}", flush=True)
    job = request(
        "POST",
        "/batches",
        {
            "input_file_id": uploaded["id"],
            "endpoint": "/v1/embeddings",
            "completion_window": args.window,
            "metadata": {"ds_name": args.name, "ds_description": args.description},
        },
        key=key,
    )
    write_job(directory, job)
    print(f"batch_id={job['id']} status={job['status']}")


def cmd_status(args):
    directory = Path(args.dir)
    job = read_job(directory)
    while True:
        job = request("GET", "/batches/" + job["id"])
        write_job(directory, job)
        counts = job.get("request_counts") or {}
        print(
            f"{time.strftime('%H:%M:%S')} {job['status']} "
            f"completed={counts.get('completed', 0)}/{counts.get('total', 0)} "
            f"failed={counts.get('failed', 0)}",
            flush=True,
        )
        if job["status"] in TERMINAL or not args.wait:
            break
        time.sleep(args.interval)
    if job["status"] == "failed":
        print(json.dumps(job.get("errors"), ensure_ascii=False))


def cmd_fetch(args):
    import numpy as np

    directory = Path(args.dir)
    job = read_job(directory)
    job = request("GET", "/batches/" + job["id"])
    write_job(directory, job)
    if job["status"] != "completed":
        sys.exit(f"job status is {job['status']}, not completed")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    key = api_key()
    if job.get("error_file_id"):
        req = urllib.request.Request(
            api_root() + "/files/" + job["error_file_id"] + "/content",
            headers={"Authorization": "Bearer " + key},
        )
        with urllib.request.urlopen(req, timeout=300) as response:
            errors = response.read().decode("utf-8")
        (directory / "error.jsonl").write_text(errors, encoding="utf-8")
        print(errors[:2000])
        sys.exit("batch has failed requests; see error.jsonl")
    req = urllib.request.Request(
        api_root() + "/files/" + job["output_file_id"] + "/content",
        headers={"Authorization": "Bearer " + key},
    )
    print("downloading results ...", flush=True)
    with urllib.request.urlopen(req, timeout=1200) as response:
        raw = response.read().decode("utf-8")
    (directory / "output.jsonl").write_text(raw, encoding="utf-8")
    vectors = {}
    total_tokens = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        resp = item.get("response") or {}
        if resp.get("status_code") != 200:
            sys.exit(f"non-200 result for {item.get('custom_id')}: {str(item)[:300]}")
        body = resp["body"]
        total_tokens += (body.get("usage") or {}).get("total_tokens", 0)
        vectors[item["custom_id"]] = body["data"][0]["embedding"]
    expected = set(manifest["chunk_ids"])
    missing = expected - set(vectors)
    extra = set(vectors) - expected
    if missing or extra:
        sys.exit(f"id mismatch: missing={len(missing)} extra={len(extra)}; e.g. {list(missing)[:3]}")
    ids = manifest["chunk_ids"]
    matrix = np.asarray([vectors[cid] for cid in ids], dtype=np.float32)
    if matrix.shape != (len(ids), manifest["dim"]):
        sys.exit(f"unexpected matrix shape {matrix.shape}")
    norms = np.linalg.norm(matrix, axis=1)
    if float(norms.min()) < 0.5 or float(norms.max()) > 1.5:
        sys.exit(f"vector norms out of range: {norms.min()}..{norms.max()}")
    out_path = directory / "vectors.npz"
    np.savez_compressed(out_path, ids=np.asarray(ids), vectors=matrix)
    print(
        f"saved {out_path}: {matrix.shape}, billed_tokens={total_tokens:,}, "
        f"norm {norms.min():.4f}..{norms.max():.4f}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("generate")
    gen.add_argument("--chunks", nargs="+", required=True)
    gen.add_argument("--dir", required=True)
    gen.add_argument("--model", default=MODEL_DEFAULT)
    gen.add_argument("--dim", type=int, default=1024)
    gen.set_defaults(func=cmd_generate)

    submit = sub.add_parser("submit")
    submit.add_argument("--dir", required=True)
    submit.add_argument("--window", default="24h")
    submit.add_argument("--name", default="cuc-kb-embed")
    submit.add_argument("--description", default="CUC KB document embeddings")
    submit.set_defaults(func=cmd_submit)

    status = sub.add_parser("status")
    status.add_argument("--dir", required=True)
    status.add_argument("--wait", action="store_true")
    status.add_argument("--interval", type=int, default=60)
    status.set_defaults(func=cmd_status)

    fetch = sub.add_parser("fetch")
    fetch.add_argument("--dir", required=True)
    fetch.set_defaults(func=cmd_fetch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()