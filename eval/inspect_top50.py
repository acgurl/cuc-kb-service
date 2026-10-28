import json
import os
import urllib.request

token = os.environ["KB_API_TOKEN"]
queries = [
    ("u06", "申请推免需要满足哪些基本条件？"),
    ("u08", "重修、重考取得的必修课学分最多认定多少？"),
    ("g04", "研究生提出学位申请前要完成哪些环节？"),
    ("t18", "多少金额以上的修缮工程由校园建设处负责？"),
    ("s10", "一卡通"),
    ("t02", "教授岗位聘用需要满足哪些条件？"),
    ("u29", "宿舍报修可以通过什么渠道？"),
    ("u17", "学生在校医院门诊看病需要自付多少？"),
]
for case_id, query in queries:
    payload = {"query": query, "top_k": 20}
    request = urllib.request.Request(
        "http://127.0.0.1:8000/v1/search",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    response = json.loads(urllib.request.urlopen(request, timeout=120).read())
    print("\n=====", case_id, query, "no_answer=", response["no_answer"], "conf=", response["confidence"])
    for rank, item in enumerate(response["results"], 1):
        citation = item["citation"]
        if rank <= 20 or any(
            key in citation["doc_title"]
            for key in ["2025〕7号", "2026〕219号", "2024〕231号", "2019〕169号", "2023〕139号", "2019〕124号"]
        ):
            print("  %d. [%s] p%s dense=%s rerank=%s generic=%s" % (
                rank, citation["doc_title"][:58], citation.get("page_start"),
                item.get("dense_score"), item.get("rerank_score"), item.get("is_generic"),
            ))
