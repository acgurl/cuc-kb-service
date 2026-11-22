"""Sequential version regression through the live HTTP MCP endpoint."""

import argparse
import json
import math
import os
import statistics
import time
from pathlib import Path
from urllib.request import Request, urlopen

from freshness_check import metrics


def rpc(url, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": "Bearer " + os.environ["KB_MCP_TOKEN"],
    }
    with urlopen(Request(url, body, headers), timeout=18) as response:
        if "application/json" in response.headers.get("Content-Type", ""):
            reply = json.load(response)
        else:
            reply = next(json.loads(line[6:]) for line in response if line.startswith(b"data: "))
    if reply.get("error") or reply["result"].get("isError"):
        raise RuntimeError(str(reply)[:250])
    return reply["result"]


def search(url, query):
    started = time.perf_counter()
    response = rpc(url, "tools/call", {"name": "kb_search", "arguments": {"query": query, "top_k": 5}})
    result = json.loads(response["content"][0]["text"])
    result["wall_ms"] = round((time.perf_counter() - started) * 1000)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("datasets", nargs="+", type=Path)
    args = parser.parse_args()
    cases = [case for path in args.datasets for case in json.loads(path.read_text(encoding="utf-8"))]
    handshake = rpc(args.url, "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "freshness-live-eval", "version": "1"},
    })
    print("version guidance:", "区分版本" in handshake.get("instructions", ""), flush=True)
    search(args.url, "学校图书馆借阅规则")  # Warm the connection, not the benchmark queries.
    rows = []
    with args.output.with_suffix(".jsonl").open("x", encoding="utf-8") as log:
        for case in cases:
            row = {"case": case}
            try:
                row["live"] = search(args.url, case["query"])
            except (RuntimeError, OSError, ValueError, KeyError, StopIteration) as exc:
                row["error"] = str(exc)
            rows.append(row)
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            print(case["id"], row.get("live", {}).get("wall_ms", "error"), flush=True)
    good = [row for row in rows if "live" in row]
    summary = metrics(good, "live") if good else {}
    times = sorted(row["live"]["wall_ms"] for row in good)
    if times:
        summary["mcp_wall_ms"] = {"p50": statistics.median(times), "p95": times[math.ceil(.95 * len(times)) - 1],
                                  "over_2s": sum(t > 2000 for t in times), "max": times[-1]}
    summary["errors"] = len(rows) - len(good)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump({"summary": summary, "rows": rows}, out, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
