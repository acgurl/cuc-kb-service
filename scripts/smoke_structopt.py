#!/usr/bin/env python3
import json, os, urllib.request
from pathlib import Path
API = "http://127.0.0.1:8000"
TOKEN = os.environ["KB_API_TOKEN"]
m = json.loads(Path("batch-structopt-20261108/manifest.json").read_text(encoding="utf-8"))
sup = set(m["superseded"])

def search(q, k=8):
    req = urllib.request.Request(API + "/v1/search",
        data=json.dumps({"query": q, "top_k": k}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

queries = [
    ("宿舍床尺寸是多少", None),
    ("数据安全法", "dbd15667ee747"),          # drop dup must not appear
    ("采购管理办法", "d131b5234820e"),          # drop dup (protected, kept) - just show
    ("公寓维修漏水感谢信", "d5499e1cf315e"),    # marked news must not leak
    ("学位授权点建设年度报告 公共管理", "d728ac4398c50"),  # marked report must not leak
    ("研究生学位论文编写规则", None),
    ("转专业报名", None),
]
leaks = []
for q, forbid in queries:
    b = search(q)
    res = b.get("results") or []
    print(f"\nQ: {q}  no_answer={b.get('no_answer')} n={len(res)}")
    for r in res[:5]:
        did = r.get("doc_id")
        mark = " [SUPERSEDED!]" if did in sup else ""
        print(f"   {did} {r.get('doc_title','')[:45]} score={r.get('rerank_score')}{mark}")
        if did in sup:
            leaks.append((q, did))
    if forbid:
        if any(r.get("doc_id") == forbid for r in res):
            print(f"   !! FORBIDDEN {forbid} present")
print("\n=== superseded leaks into results:", len(leaks), "===")
for q, did in leaks:
    print("  LEAK", q, did)