"""Shared helpers for the eval scripts (HTTP client and retrieval metrics)."""

import json
import os
import re
import urllib.request

DEFAULT_API = os.getenv("KB_API_URL", "http://127.0.0.1:8000")


def norm(text):
    return re.sub(r"\s+", "", text or "")


def request_json(api, token, path, payload=None, timeout=90):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        api.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def search(api, token, query, assistant=None, top_k=5, timeout=90):
    payload = {"query": query, "top_k": top_k}
    if assistant:
        payload["assistant"] = assistant
    return request_json(api, token, "/v1/search", payload, timeout=timeout)


def percentile(values, pct):
    """Nearest-rank percentile with rounding (used by eval_v3 and later)."""
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


def keyword_hit(results, keywords, top=None):
    texts = [norm(item.get("text", "")) for item in results[:top]]
    return any(norm(k) in text for k in keywords for text in texts)


def top_title(results):
    return results[0]["citation"]["doc_title"] if results else None
