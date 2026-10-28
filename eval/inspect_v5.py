import json
import os
import urllib.request

token = os.environ["KB_API_TOKEN"]
queries = json.load(open("/tmp/inspect_q.json", encoding="utf-8"))


def call(query, assistant=None, top=5):
    payload = {"query": query, "top_k": top}
    if assistant:
        payload["assistant"] = assistant
    request = urllib.request.Request(
        "http://127.0.0.1:8000/v1/search",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    return json.loads(urllib.request.urlopen(request, timeout=120).read())


for case_id, query, assistant in queries:
    response = call(query, assistant)
    print("\n=====", case_id, query, "| assistant=", assistant,
          "| no_answer=", response["no_answer"], "conf=", response["confidence"])
    for rank, item in enumerate(response["results"][:5], 1):
        citation = item["citation"]
        text = " ".join(item["text"].split())
        print("  %d. [%s] p%s dense=%s rerank=%s :: %s" % (
            rank, citation["doc_title"][:40], citation.get("page_start"),
            item.get("dense_score"), item.get("rerank_score"), text[:170],
        ))
